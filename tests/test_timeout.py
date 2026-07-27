# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Timeout tests: firing, the silent ``Timeout(s, False)`` sentinel, nesting,
cancel/pending, with_timeout value + timeout_value, and -- crucially -- a
Timeout interrupting a real blocking wait on a cooperative lock/queue.
"""

from __future__ import absolute_import

import pytest

import filament
from filament import Timeout


def test_timeout_fires():
    def body():
        with Timeout(0.02):
            filament.sleep(5)

    with pytest.raises(Timeout):
        filament.spawn(body).wait()


def test_timeout_does_not_fire_when_fast():
    def body():
        with Timeout(1.0):
            filament.sleep(0)
            return "done"

    assert filament.spawn(body).wait() == "done"


def test_timeout_none_never_fires():
    def body():
        with Timeout(None):
            filament.sleep(0.01)
            return "ok"

    assert filament.spawn(body).wait() == "ok"


def test_timeout_silent_sentinel():
    # Timeout(seconds, False) -> on expiry the with-block just exits quietly.
    reached_after = []

    def body():
        with Timeout(0.02, False):
            filament.sleep(5)
        reached_after.append(True)
        return "survived"

    assert filament.spawn(body).wait() == "survived"
    assert reached_after == [True]


def test_timeout_custom_exception():
    class MyErr(Exception):
        pass

    def body():
        with Timeout(0.02, MyErr):
            filament.sleep(5)

    with pytest.raises(MyErr):
        filament.spawn(body).wait()


def test_nested_timeouts_inner_fires_outer_survives():
    outer_fired = []
    inner_fired = []

    def body():
        with Timeout(5.0):                    # outer: long
            try:
                with Timeout(0.02):           # inner: short, should fire
                    filament.sleep(5)
            except Timeout:
                inner_fired.append(True)
            # Outer still armed and not expired -> we continue normally.
            filament.sleep(0)
            return "outer-ok"

    assert filament.spawn(body).wait() == "outer-ok"
    assert inner_fired == [True]
    assert outer_fired == []


def test_timeout_cancel_and_pending():
    def body():
        t = Timeout(0.05)
        t.start()
        assert t.pending is True
        t.cancel()
        assert t.pending is False
        filament.sleep(0.1)   # would have fired if not cancelled
        return "no-fire"

    assert filament.spawn(body).wait() == "no-fire"


def test_with_timeout_returns_value():
    def work():
        filament.sleep(0)
        return 55

    assert filament.spawn(
        lambda: filament.with_timeout(1.0, work)).wait() == 55


def test_with_timeout_returns_timeout_value_on_expiry():
    def work():
        filament.sleep(5)
        return "never"

    def body():
        return filament.with_timeout(0.02, work, timeout_value="fallback")

    assert filament.spawn(body).wait() == "fallback"


def test_with_timeout_raises_without_timeout_value():
    def work():
        filament.sleep(5)

    with pytest.raises(Timeout):
        filament.spawn(lambda: filament.with_timeout(0.02, work)).wait()


def test_timeout_interrupts_blocking_lock():
    from _filament import locking

    def body():
        lock = locking.Lock()
        lock.acquire()  # held by us; a second acquire would block forever
        out = []

        def other():
            try:
                with Timeout(0.05):
                    lock.acquire()      # blocks; Timeout must throw in
                out.append("acquired")
            except Timeout:
                out.append("interrupted")
        filament.spawn(other).wait()
        return out

    assert filament.spawn(body).wait() == ["interrupted"]


def test_timeout_interrupts_blocking_queue_get():
    from _filament import queue

    def body():
        q = queue.Queue()
        out = []

        def other():
            try:
                with Timeout(0.05):
                    q.get()             # blocks forever; Timeout interrupts
                out.append("got")
            except Timeout:
                out.append("interrupted")
        filament.spawn(other).wait()
        return out

    assert filament.spawn(body).wait() == ["interrupted"]


def test_timeout_is_exception_subclass():
    # A single ``except filament.exc.Timeout`` must catch a context Timeout.
    from filament import exc
    assert issubclass(Timeout, exc.Timeout)


def test_timeout_repr_and_str():
    t = Timeout(3)
    assert "3" in str(t)
    assert "Timeout" in repr(t)
    assert str(Timeout(None)) == ""


def _queue_depth():
    """(ready-now events, events waiting on a deadline) in this scheduler."""
    return filament.Scheduler().queue_depth()


def test_cancel_drains_the_timer_queue():
    # cancel() has to take the event back out of the scheduler.  Leaving it
    # queued until its original deadline (which is what filament used to do)
    # costs a node plus a reference for the whole timeout -- and code that
    # arms a timeout per operation, like every HTTP client, then grows the
    # timer queue without bound.
    before_immediate, before_timers = _queue_depth()

    timeouts = [Timeout(3600) for _ in range(64)]
    for timeout in timeouts:
        timeout.start()
    assert _queue_depth()[1] == before_timers + 64

    for timeout in timeouts[:32]:
        timeout.cancel()
    assert _queue_depth()[1] == before_timers + 32

    for timeout in timeouts[32:]:
        timeout.cancel()
    assert _queue_depth() == (before_immediate, before_timers)

    # Cancelling twice, or after the fact, stays harmless.
    timeouts[0].cancel()
    assert _queue_depth() == (before_immediate, before_timers)


def test_cancel_releases_the_callback():
    # The queued event holds a reference to the timer's callback; cancelling
    # has to drop it rather than wait for the deadline.
    import gc
    import weakref

    class Callback(object):
        def __call__(self):
            pass

    callback = Callback()
    ref = weakref.ref(callback)
    timer = filament.Timer(3600, callback)
    del callback

    gc.collect()
    assert ref() is not None                 # the queued event still holds it
    timer.cancel()
    gc.collect()
    assert ref() is None


def test_timers_fire_in_deadline_order():
    # The timer queue is a heap; deadlines must come out earliest-first no
    # matter what order they went in.
    fired = []
    delays = [0.05, 0.01, 0.04, 0.02, 0.03, 0.015, 0.045]
    timers = [filament.Timer(delay, fired.append, delay) for delay in delays]
    filament.sleep(0.15)
    assert fired == sorted(delays), fired
    for timer in timers:
        timer.cancel()


def test_expired_timer_does_not_starve_behind_yields():
    # Ready-now wakeups and expired timers both run in the same scheduler
    # pass, so a greenthread spinning on sleep(0) cannot hold a timer off.
    fired = []
    spins = []

    def spinner():
        while not fired:
            spins.append(1)
            filament.sleep(0)

    timer = filament.Timer(0.02, fired.append, 'fired')
    filament.spawn(spinner).wait()
    assert fired == ['fired']
    assert len(spins) > 1                    # it really was spinning
    timer.cancel()
