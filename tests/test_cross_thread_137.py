# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
THE HEADLINE REGRESSION TEST -- filament must NOT have the historical eventlet
cross-thread greenlet-switch / logging-deadlock bug.

Background and citations
========================
This reproduces the exact scenario behind a family of well-documented
production failures:

  * **eventlet Bitbucket issue #137** -- "Use of threading[ locks] ... causes
    deadlock".  Logging (or any code) that holds a native ``threading`` lock
    across a call into the hub could deadlock, because a greenlet blocked on a
    *native* lock never yields back to the hub, so the greenlet that would
    release the lock never runs.

  * **OpenStack Nova commit e4b0d8e944** (``Closes-Bug: LP #1252409``) and Nova
    commit **7c30da1384** -- Nova hit this in the wild: logging from a real OS
    thread (e.g. a native thread-pool worker) while eventlet ran greenthreads
    could raise ``greenlet.error: cannot switch to a different thread`` or
    deadlock the whole worker.

  * **eventlet GitHub issue #432** -- the same cross-thread switch hazard tracked
    upstream after the Bitbucket migration.

  * **comstud's first fix attempt, eventlet commit f5a7eaacf78e** -- Chris
    Behrens' original mitigation, which reworked eventlet's locks around a
    ``threading.Condition`` so a greenlet waiting on a lock could be woken from
    another thread without an illegal cross-thread greenlet switch.

Why filament is immune (and cheaper)
====================================
filament gives **each OS thread its own scheduler**.  A greenlet is only ever
switched by the scheduler of the thread that owns it; a cross-thread wakeup is
never performed by directly switching a greenlet owned by another OS thread.
Instead, the wakeup is *deferred onto the owning thread's scheduler event queue*
(the same mechanism ``filament.tpool`` uses to deliver a worker thread's result
back to the calling greenthread).  Because the owning scheduler performs the
switch cooperatively, there is never a ``greenlet.error: cannot switch to a
different thread`` and never a native-lock deadlock -- and it costs only a queue
hand-off, cheaper than comstud's ``threading.Condition``-based eventlet fix.

``filament.patcher.patch_thread(logging=True, existing_locks=True)`` converts the
already-created ``logging._lock`` and per-handler ``.lock`` objects into
filament cooperative locks, which is the piece that closes the #137 hole.

Test strategy
=============
The scenario mutates process-global state (patch_thread, logging locks) and is
exactly the thing that historically *hung*, so we run it in a **fresh
subprocess** with THREE independent guards against a hang being mistaken for a
pass:

  1. an in-process ``threading.Timer`` watchdog that hard-exits with a distinct
     ``WATCHDOG-DEADLOCK`` marker + code if the scenario runs too long;
  2. a ``filament.Timeout`` around the main driver flow;
  3. ``run_py``'s outer wall-clock subprocess timeout.

A deadlock therefore becomes a deterministic TEST FAILURE, never an infinite
hang.  The child also captures any ``greenlet.error`` raised anywhere (in main
greenthreads or in tpool worker bodies) and fails if one occurred.
"""

from __future__ import absolute_import

from tests._helpers import run_py


# The scenario, parameterized by:
#   M     -- number of main-thread greenthreads that log in a loop
#   K     -- number of tpool (real OS thread) workers that log in a loop
#   ITERS -- iterations each performs
#   WD    -- internal watchdog seconds
_SCENARIO = r'''
import sys
import threading

import filament.patcher as patcher
# The load-bearing call: convert logging's already-created native locks into
# filament cooperative locks (the #137 fix).
patcher.patch_thread(logging=True, existing_locks=True)

import logging
import greenlet
import filament
import filament.tpool as tpool

M = __M__
K = __K__
ITERS = __ITERS__
WATCHDOG_SECS = __WD__

records = []          # shared log buffer
greenlet_errors = []  # any greenlet.error captured anywhere
other_errors = []
counter = [0]         # main-greenthread progress counter


class BufferHandler(logging.Handler):
    """Custom handler recording every emitted message into a shared list."""
    def emit(self, record):
        try:
            records.append(record.getMessage())
        except Exception as e:  # pragma: no cover
            other_errors.append("emit:%r" % (e,))


logger = logging.getLogger("cx137")
logger.setLevel(logging.DEBUG)
logger.addHandler(BufferHandler())

# The handler was created AFTER patch_thread swept existing locks, so re-run the
# existing-lock conversion to green this handler's freshly-minted lock too.
patcher._patch_existing_locks(logging=True, existing_locks=True)

# Confirm the module-level logging lock is now a filament cooperative lock -- if
# this is still native, the #137 hole is open.
_lock_type = type(logging._lock).__module__
if "filament" not in _lock_type:
    sys.stderr.write("LOGGING-LOCK-NOT-GREENED:%s\n" % (_lock_type,))
    sys.stdout.flush()
    import os
    os._exit(4)


def main_greenthread(idx):
    try:
        for i in range(ITERS):
            logger.info("main-%d-%d" % (idx, i))
            counter[0] += 1
            filament.sleep(0)      # yield so workers/other greenthreads run
    except greenlet.error as e:
        greenlet_errors.append("main:%r" % (e,))
    except BaseException as e:
        other_errors.append("main:%r" % (e,))


def worker_body(idx):
    # Runs on a REAL OS thread (tpool worker). This is the historically-fatal
    # spot: logging (grabbing the logging mutex) from a native thread while the
    # hub runs greenthreads.
    for i in range(ITERS):
        logger.info("worker-%d-%d" % (idx, i))


def run_worker(idx):
    try:
        tpool.execute(worker_body, idx)
    except greenlet.error as e:
        greenlet_errors.append("worker:%r" % (e,))
    except BaseException as e:
        other_errors.append("worker:%r" % (e,))


def driver():
    # Hard cap on the whole flow: a filament Timeout is our second guard.
    with filament.Timeout(WATCHDOG_SECS - 2, False):
        mains = [filament.spawn(main_greenthread, k) for k in range(M)]
        workers = [filament.spawn(run_worker, k) for k in range(K)]
        filament.joinall(mains)
        for w in workers:
            w.wait()


# Guard #1: a real-thread watchdog. If the scenario deadlocks, we hard-exit with
# a distinctive marker instead of hanging.
def _watchdog():
    sys.stderr.write("WATCHDOG-DEADLOCK\n")
    sys.stderr.flush()
    import os
    os._exit(3)


wd = threading.Timer(WATCHDOG_SECS, _watchdog)
wd.daemon = True
wd.start()

filament.spawn(driver).wait()
wd.cancel()

# ---- assertions --------------------------------------------------------------
expected_main = M * ITERS
expected_worker = K * ITERS
main_logs = [r for r in records if r.startswith("main-")]
worker_logs = [r for r in records if r.startswith("worker-")]

assert not greenlet_errors, "greenlet.error(s): %r" % (greenlet_errors[:5],)
assert not other_errors, "unexpected error(s): %r" % (other_errors[:5],)
# No deadlock: we got here, and every expected record is present.
assert len(main_logs) == expected_main, (len(main_logs), expected_main)
assert len(worker_logs) == expected_worker, (len(worker_logs), expected_worker)
# Main-thread greenthreads made progress concurrently with the tpool logging.
assert counter[0] == expected_main, (counter[0], expected_main)

sys.stdout.write("PASS main=%d worker=%d counter=%d\n"
                 % (len(main_logs), len(worker_logs), counter[0]))
sys.stdout.flush()
import os
os._exit(0)
'''


def _run_scenario(M, K, ITERS, WD, outer_timeout):
    script = (_SCENARIO
              .replace("__M__", str(M))
              .replace("__K__", str(K))
              .replace("__ITERS__", str(ITERS))
              .replace("__WD__", str(WD)))
    res = run_py(script, timeout=outer_timeout)
    # A hang manifests as either run_py's own timeout or the internal watchdog.
    assert not res.timed_out, "scenario HUNG (subprocess killed)\n" + repr(res)
    assert "WATCHDOG-DEADLOCK" not in res.stderr, \
        "internal watchdog fired -> DEADLOCK\n" + repr(res)
    assert res.returncode == 0, repr(res)
    assert "PASS" in res.stdout, repr(res)
    return res


def test_no_cross_thread_greenlet_error_basic():
    """The core #137 scenario: logging from tpool workers + hub greenthreads.

    Asserts no ``greenlet.error``, no deadlock, all log records present, and
    that main-thread greenthreads advanced concurrently with the worker logging.
    """
    res = _run_scenario(M=3, K=3, ITERS=50, WD=15, outer_timeout=25)
    # Sanity-check the reported counts too.
    assert "main=150 worker=150 counter=150" in res.stdout, repr(res)


def test_cross_thread_137_stress():
    """Stress variant: many greenthreads + many workers, thousands of log lines.

    M=8 main greenthreads and K=8 tpool workers each emit 300 log records
    (4800 total) concurrently.  Must complete cleanly and deterministically.
    """
    res = _run_scenario(M=8, K=8, ITERS=300, WD=30, outer_timeout=45)
    assert "main=2400 worker=2400 counter=2400" in res.stdout, repr(res)
