# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Event (settable flag) and AsyncResult (one-shot value/exception future) tests.

Covers the gevent-shaped API (set/clear/wait, set_exception/get/link) plus the
eventlet aliases carried on AsyncResult (send/send_exception/reset).
"""

from __future__ import absolute_import

import pytest

import filament
from filament import exc


# --------------------------------------------------------------------------- #
# Event
# --------------------------------------------------------------------------- #

def test_event_starts_unset():
    ev = filament.Event()
    assert ev.is_set() is False
    assert ev.ready() is False


def test_event_multi_waiter_wake():
    ev = filament.Event()
    got = []

    def waiter(i):
        got.append((i, ev.wait()))

    gs = [filament.spawn(waiter, i) for i in range(10)]
    filament.sleep(0)  # park them all in wait()
    assert got == []
    ev.set()
    filament.joinall(gs)
    assert len(got) == 10
    assert all(result is True for _, result in got)


def test_event_set_is_sticky_for_late_waiters():
    ev = filament.Event()
    ev.set()
    # A waiter arriving after set() returns immediately.
    assert filament.spawn(ev.wait).wait() is True


def test_event_clear_and_reset():
    ev = filament.Event()
    ev.set()
    assert ev.is_set() is True
    ev.clear()
    assert ev.is_set() is False

    got = []
    g = filament.spawn(lambda: got.append(ev.wait()))
    filament.sleep(0)
    assert got == []          # blocked again after clear()
    ev.set()
    g.wait()
    assert got == [True]


def test_event_wait_timeout_returns_false():
    ev = filament.Event()
    # Never set; wait() returns False on timeout (never raises).
    assert filament.spawn(lambda: ev.wait(0.02)).wait() is False


def test_event_isSet_alias():
    ev = filament.Event()
    assert ev.isSet() is False
    ev.set()
    assert ev.isSet() is True


# --------------------------------------------------------------------------- #
# AsyncResult
# --------------------------------------------------------------------------- #

def test_asyncresult_value():
    ar = filament.AsyncResult()

    def setter():
        ar.set(123)

    filament.spawn_n(setter)
    assert filament.spawn(ar.get).wait() == 123
    assert ar.ready() is True
    assert ar.successful() is True
    assert ar.value == 123
    assert ar.exception is None


def test_asyncresult_exception_reraised():
    ar = filament.AsyncResult()
    filament.spawn_n(lambda: ar.set_exception(ValueError("boom")))
    with pytest.raises(ValueError):
        filament.spawn(ar.get).wait()
    assert ar.ready() is True
    assert ar.successful() is False
    assert isinstance(ar.exception, ValueError)


def test_asyncresult_exception_preserves_traceback():
    ar = filament.AsyncResult()

    def make():
        try:
            raise RuntimeError("tb-here")
        except RuntimeError:
            import sys
            ar.set_exception(sys.exc_info()[1], sys.exc_info())

    filament.spawn(make).wait()
    try:
        ar.get()
        assert False
    except RuntimeError:
        import sys
        tb = sys.exc_info()[2]
        names = []
        while tb is not None:
            names.append(tb.tb_frame.f_code.co_name)
            tb = tb.tb_next
        assert "make" in names


def test_asyncresult_get_nowait_not_ready():
    ar = filament.AsyncResult()
    # get()/get_nowait() raise the base exc.Timeout (not the Timeout subclass).
    with pytest.raises(exc.Timeout):
        ar.get_nowait()


def test_asyncresult_get_nowait_ready():
    ar = filament.AsyncResult()
    ar.set("v")
    assert ar.get_nowait() == "v"


def test_asyncresult_get_timeout():
    ar = filament.AsyncResult()
    with pytest.raises(exc.Timeout):
        filament.spawn(lambda: ar.get(timeout=0.02)).wait()


def test_asyncresult_wait_returns_value_never_raises():
    ar = filament.AsyncResult()
    filament.spawn_n(lambda: ar.set_exception(KeyError("k")))
    # wait() (unlike get()) returns the value (None here) and never raises.
    assert filament.spawn(lambda: ar.wait()).wait() is None


def test_asyncresult_set_twice_raises():
    ar = filament.AsyncResult()
    ar.set(1)
    with pytest.raises(RuntimeError):
        ar.set(2)


def test_asyncresult_link_fires():
    ar = filament.AsyncResult()
    fired = []
    ar.link(lambda a: fired.append(a.value))
    filament.spawn_n(lambda: ar.set(7))
    filament.sleep(0)
    filament.sleep(0)
    assert fired == [7]


def test_asyncresult_link_after_ready_fires_immediately():
    ar = filament.AsyncResult()
    ar.set(5)
    fired = []
    ar.link(lambda a: fired.append(a.value))
    filament.sleep(0)
    filament.sleep(0)
    assert fired == [5]


# --------------------------------------------------------------------------- #
# eventlet aliases on AsyncResult
# --------------------------------------------------------------------------- #

def test_asyncresult_send_alias():
    ar = filament.AsyncResult()
    filament.spawn_n(lambda: ar.send("hello"))
    assert filament.spawn(ar.wait).wait() == "hello"


def test_asyncresult_send_exception_alias():
    ar = filament.AsyncResult()
    filament.spawn_n(lambda: ar.send_exception(ValueError("e")))
    with pytest.raises(ValueError):
        filament.spawn(ar.get).wait()


def test_asyncresult_reset_allows_reuse():
    ar = filament.AsyncResult()
    ar.set(1)
    assert ar.ready() is True
    ar.reset()
    assert ar.ready() is False
    ar.set(2)
    assert ar.get_nowait() == 2
