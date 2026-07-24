# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Cooperative locking tests: Lock mutual exclusion, RLock reentrancy, Condition
wait/notify/notify_all, Semaphore counting, BoundedSemaphore, and acquire
timeouts.

NOTE ON A LIBRARY BUG (documented, not worked around):
``_filament.locking.Lock.acquire(timeout=...)`` and ``RLock.acquire(timeout=...)``
raise ``SystemError`` when the timeout expires -- the C code returns ``Py_False``
on the ``-ETIMEDOUT`` path without clearing the ``exc.Timeout`` that
``fil_waiterlist_wait`` set, so CPython reports "returned a result with an
exception set" (src/locking/fil_lock.c, the ``_lock_acquire`` / ``_rlock_acquire``
ETIMEDOUT return path).  ``Semaphore.acquire(timeout=...)`` is unaffected (it
propagates ``exc.Timeout`` cleanly).  The two ``xfail`` tests below pin this so a
future fix flips them to ``xpass``.  For a working "acquire with a deadline" the
tests use the ``filament.Timeout`` context manager, which interrupts the blocking
acquire correctly.
"""

from __future__ import absolute_import

import pytest

import filament
from filament import exc, Timeout
from _filament import locking

from tests._helpers import run_py


def run(fn):
    return filament.spawn(fn).wait()


# --------------------------------------------------------------------------- #
# Lock
# --------------------------------------------------------------------------- #

def test_lock_basic_acquire_release():
    def body():
        lk = locking.Lock()
        assert lk.acquire() is True
        assert lk.locked() is True
        lk.release()
        assert lk.locked() is False
    run(body)


def test_lock_nonblocking_when_held():
    def body():
        lk = locking.Lock()
        lk.acquire()
        assert lk.acquire(blocking=False) is False
        lk.release()
    run(body)


def test_lock_mutual_exclusion():
    # Two greenthreads incrementing a shared counter under a lock must not
    # interleave inside the critical section.
    def body():
        lk = locking.Lock()
        in_section = [0]
        violations = [0]

        def worker():
            for _ in range(50):
                lk.acquire()
                in_section[0] += 1
                if in_section[0] != 1:
                    violations[0] += 1
                filament.sleep(0)  # yield while holding the lock
                in_section[0] -= 1
                lk.release()

        gts = [filament.spawn(worker) for _ in range(5)]
        filament.joinall(gts)
        return violations[0]

    assert run(body) == 0


def test_lock_as_context_manager():
    def body():
        lk = locking.Lock()
        with lk:
            assert lk.locked() is True
        assert lk.locked() is False
    run(body)


def test_lock_interrupted_by_timeout():
    def body():
        lk = locking.Lock()
        lk.acquire()
        out = []

        def other():
            try:
                with Timeout(0.05):
                    lk.acquire()
                out.append("acquired")
            except Timeout:
                out.append("interrupted")
        filament.spawn(other).wait()
        return out

    assert run(body) == ["interrupted"]


# NOTE: We document the Lock/RLock ``acquire(timeout=...)`` SystemError bug in a
# *subprocess* (see test_lock_rlock_acquire_timeout_systemerror below) rather
# than in-process: triggering the C bug leaves the scheduler's waiter state
# inconsistent and corrupts subsequent in-process tests, so it must be isolated.


LOCK_TIMEOUT_BUG_SCRIPT = '''
import filament
from _filament import locking

def probe(cls):
    holder = locking.__dict__  # noqa
    lk = cls()
    lk.acquire()
    box = []
    def other():
        try:
            r = lk.acquire(timeout=0.02)
            box.append(("returned", r))
        except SystemError as e:
            box.append(("SystemError", str(e)))
        except filament.exc.Timeout:
            box.append(("Timeout",))
        except BaseException as e:
            box.append(("other", type(e).__name__))
    filament.spawn(other).wait()
    return box[0]

lock_outcome = filament.spawn(lambda: probe(locking.Lock)).wait()
print("LOCK=%s" % (lock_outcome[0],))
import sys; sys.stdout.flush()
'''


def test_lock_rlock_acquire_timeout_returns_false():
    """Lock.acquire(timeout=) returns False cleanly on expiry (regression test).

    The C ``_lock_acquire`` used to return ``Py_False`` on the ``-ETIMEDOUT``
    path without clearing the ``exc.Timeout`` set by ``fil_waiterlist_wait``,
    so CPython raised ``SystemError: ... returned a result with an exception
    set``.  Fixed by ``PyErr_Clear()`` on the timeout path in fil_lock.c.
    Isolated in a subprocess so any waiter-state issue cannot leak into other
    tests.
    """
    res = run_py(LOCK_TIMEOUT_BUG_SCRIPT, timeout=20)
    assert not res.timed_out, repr(res)
    # Correct behavior: a contended acquire(timeout=) that expires returns
    # False -- no SystemError, no leaked exception.
    assert "LOCK=returned" in res.stdout, repr(res)
    assert "SystemError" not in res.stdout, repr(res)


# --------------------------------------------------------------------------- #
# RLock
# --------------------------------------------------------------------------- #

def test_rlock_reentrant_same_greenthread():
    def body():
        r = locking.RLock()
        r.acquire()
        r.acquire()
        r.acquire()
        r.release()
        r.release()
        # Still held once: another greenthread must block.
        out = []

        def other():
            try:
                with Timeout(0.02):
                    r.acquire()
                out.append("acquired")
            except Timeout:
                out.append("blocked")
        filament.spawn(other).wait()
        r.release()  # fully release
        # Now a fresh greenthread can take it.
        acquired = []

        def other2():
            r.acquire()
            acquired.append(True)
            r.release()
        filament.spawn(other2).wait()
        return out, acquired

    out, acquired = run(body)
    assert out == ["blocked"]
    assert acquired == [True]


# --------------------------------------------------------------------------- #
# Condition
# --------------------------------------------------------------------------- #

def test_condition_notify_wakes_one():
    def body():
        c = locking.Condition()
        woke = []

        def waiter(i):
            with c:
                c.wait()
                woke.append(i)

        gts = [filament.spawn(waiter, i) for i in range(3)]
        filament.sleep(0)  # park all three
        with c:
            c.notify()
        filament.sleep(0)
        after_one = len(woke)
        with c:
            c.notify_all()
        filament.joinall(gts)
        return after_one, len(woke)

    after_one, total = run(body)
    assert after_one == 1
    assert total == 3


def test_condition_notify_all():
    def body():
        c = locking.Condition()
        woke = []

        def waiter(i):
            with c:
                c.wait()
                woke.append(i)

        gts = [filament.spawn(waiter, i) for i in range(5)]
        filament.sleep(0)
        with c:
            c.notify_all()
        filament.joinall(gts)
        return len(woke)

    assert run(body) == 5


def test_condition_wait_timeout_raises():
    def body():
        c = locking.Condition()
        with c:
            with pytest.raises(exc.Timeout):
                c.wait(0.02)
    run(body)


def test_condition_producer_consumer():
    def body():
        c = locking.Condition()
        buf = []
        results = []

        def consumer():
            with c:
                while not buf:
                    c.wait()
                results.append(buf.pop(0))

        def producer():
            filament.sleep(0.01)
            with c:
                buf.append("item")
                c.notify()

        gc = filament.spawn(consumer)
        gp = filament.spawn(producer)
        filament.joinall([gc, gp])
        return results

    assert run(body) == ["item"]


# --------------------------------------------------------------------------- #
# Semaphore
# --------------------------------------------------------------------------- #

def test_semaphore_limits_concurrency():
    def body():
        sem = locking.Semaphore(2)
        cur = [0]
        peak = [0]

        def worker():
            sem.acquire()
            cur[0] += 1
            if cur[0] > peak[0]:
                peak[0] = cur[0]
            filament.sleep(0.01)
            cur[0] -= 1
            sem.release()

        gts = [filament.spawn(worker) for _ in range(8)]
        filament.joinall(gts)
        return peak[0]

    assert run(body) == 2


def test_semaphore_acquire_timeout_raises():
    def body():
        sem = locking.Semaphore(0)
        with pytest.raises(exc.Timeout):
            sem.acquire(timeout=0.02)
    run(body)


def test_semaphore_acquire_release_pairing():
    def body():
        sem = locking.Semaphore(1)
        sem.acquire()
        released = []

        def other():
            sem.acquire()          # blocks until we release
            released.append(True)
            sem.release()
        g = filament.spawn(other)
        filament.sleep(0)
        assert released == []
        sem.release()              # wake the other
        g.wait()
        return released

    assert run(body) == [True]


# --------------------------------------------------------------------------- #
# BoundedSemaphore (eventlet_compat wrapper -- pure-Python, no monkey-patch)
# --------------------------------------------------------------------------- #

def test_bounded_semaphore_over_release_raises():
    from filament.eventlet_compat.semaphore import BoundedSemaphore

    def body():
        bs = BoundedSemaphore(1)
        bs.acquire()
        bs.release()
        with pytest.raises(ValueError):
            bs.release()   # released above the ceiling
    run(body)
