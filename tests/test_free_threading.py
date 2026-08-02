# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Free-threaded (PEP 703) builds keep the GIL disabled.

A free-threaded interpreter switches the GIL back ON, for the whole process
and for good, the moment it imports an extension module that has not declared
``Py_mod_gil = Py_MOD_GIL_NOT_USED``.  Nothing raises, nothing is logged at
default verbosity, and every other test in this suite still passes -- against
the stock runtime, having measured nothing about free-threading.  That is the
failure this file exists to catch.

Every filament extension module is imported first, not just the top-level
package, because one undeclared module anywhere is enough to flip the switch.

Inert on ordinary builds, where there is no such property to check.
"""
import sys
import sysconfig

import pytest

import filament


FREE_THREADED = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))

needs_free_threading = pytest.mark.skipif(
    not FREE_THREADED, reason="not a free-threaded build")


@needs_free_threading
def test_gil_stays_disabled_after_importing_filament():
    assert not sys._is_gil_enabled(), (
        "importing filament re-enabled the GIL: some extension module is "
        "missing Py_mod_gil = Py_MOD_GIL_NOT_USED")


@needs_free_threading
def test_gil_stays_disabled_after_importing_every_extension():
    # Importing the package does not necessarily pull in every extension.
    import filament.gevent_compat  # noqa: F401
    import filament.socket  # noqa: F401
    import filament.thread  # noqa: F401
    import filament.time  # noqa: F401
    import filament.tpool  # noqa: F401

    assert not sys._is_gil_enabled(), (
        "the GIL came back after importing filament's submodules")


def test_filament_imports():
    # Guards the two above: if the import at module scope ever fails, the
    # skipif would hide it and this file would silently test nothing.
    assert filament is not None


# ---------------------------------------------------------------------------
# Concurrency stress for the pieces whose thread-safety used to be "the GIL":
# the thread pool's registry / shutdown / run-info handshake and the timer's
# cancel path.  On a stock build these are exercised but cannot race; on a
# free-threaded build they hammer the new locking.  Kept small enough to add
# only a few seconds to the suite.
# ---------------------------------------------------------------------------

import threading


def test_thrpool_run_races_shutdown():
    from _filament.thrpool import ThreadPool

    for _ in range(5):
        tp = ThreadPool(2, 4)
        stop = threading.Event()
        errors = []

        def job(*args, **kwargs):
            # Accepts the shutdown=True kwarg a now=True shutdown delivers.
            return 42

        def hammer():
            while not stop.is_set():
                try:
                    tp.run(job, timeout=None)
                except RuntimeError:
                    # pool is (or is being) shut down: the expected loser
                    return
                except Exception as e:  # pragma: no cover - failure detail
                    errors.append(e)
                    return

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        try:
            tp.shutdown(now=True, wait=True)
        except RuntimeError:
            pass
        stop.set()
        for t in threads:
            t.join()
        assert not errors, errors


def test_thrpool_double_shutdown_single_winner():
    from _filament.thrpool import ThreadPool

    for _ in range(10):
        tp = ThreadPool(1, 2)
        outcomes = []

        def shut():
            try:
                tp.shutdown(now=True, wait=True)
                outcomes.append("ok")
            except RuntimeError:
                outcomes.append("already")

        threads = [threading.Thread(target=shut) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Exactly one shutdown may win; the rest must see "already called".
        assert outcomes.count("ok") == 1, outcomes


def test_thrpool_create_destroy_registry_churn():
    from _filament.thrpool import ThreadPool

    def churn():
        for _ in range(10):
            tp = ThreadPool(1, 2)
            tp.run(int, timeout=None)
            tp.shutdown(now=True, wait=True)

    threads = [threading.Thread(target=churn) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_thrpool_run_timeout_cancel_handshake():
    # timeout=tiny makes the waiter give up while the worker may be at any
    # stage of the job: exercises the CANCEL/DONE ownership handshake.
    from _filament.thrpool import ThreadPool
    import time as _time

    import filament.exc

    def job(*args, **kwargs):
        _time.sleep(0.0005)

    tp = ThreadPool(2, 2)
    try:
        for i in range(200):
            try:
                tp.run(job, timeout=0.0002)
            except filament.exc.Timeout:
                pass
    finally:
        tp.shutdown(now=True, wait=True)


def test_queue_chunk_churn_across_threads():
    # Each ring chunk holds 8192 items; filling past that and draining forces
    # chunk alloc/free through the shared (per-TU) freelist, which several
    # queues on several threads hit concurrently -- the shape that corrupted
    # the unlocked freelist on free-threaded builds.
    from _filament.queue import Queue

    def churn():
        q = Queue()
        for round_ in range(3):
            for i in range(9000):
                q.put(i)
            total = 0
            while not q.empty():
                total += q.get_nowait()
            assert total == sum(range(9000))

    threads = [threading.Thread(target=churn) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_timer_concurrent_cancel():
    from _filament.timer import Timer

    fired = []
    for _ in range(50):
        timer = Timer(60.0, fired.append, None)
        threads = [threading.Thread(target=timer.cancel) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert not fired
