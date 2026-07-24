# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Scheduler / greenthread tests: spawn, join, nesting, exceptions, ordering,
spawn_later/cancel, kill/killall, joinall, getcurrent identity.

All tests drive the cooperative scheduler from the main greenlet (spawn, then
wait/sleep to let the scheduler run) -- the same pattern the library's own
callers use.
"""

from __future__ import absolute_import

import pytest

import filament


def test_spawn_single_returns_result():
    g = filament.spawn(lambda: 21 * 2)
    assert g.wait() == 42


def test_spawn_1000_and_joinall():
    n = 1000
    results = []

    def work(i):
        results.append(i)
        return i

    gs = [filament.spawn(work, i) for i in range(n)]
    filament.joinall(gs)
    assert all(g.dead for g in gs)
    assert sum(g.wait() for g in gs) == sum(range(n))
    assert len(results) == n


def test_nested_spawn():
    def inner(x):
        return x + 1

    def outer(x):
        child = filament.spawn(inner, x)
        return child.wait() * 10

    assert filament.spawn(outer, 4).wait() == 50


def test_exception_reraised_by_wait():
    def boom():
        raise ValueError("kaboom")

    g = filament.spawn(boom)
    with pytest.raises(ValueError) as ei:
        g.wait()
    assert "kaboom" in str(ei.value)


def test_exception_has_traceback():
    def boom():
        raise RuntimeError("with-tb")

    g = filament.spawn(boom)
    try:
        g.wait()
        assert False, "should have raised"
    except RuntimeError:
        import sys
        tb = sys.exc_info()[2]
        # The traceback should reach back into the greenthread body (boom).
        names = []
        while tb is not None:
            names.append(tb.tb_frame.f_code.co_name)
            tb = tb.tb_next
        assert "boom" in names


def test_spawn_n_fire_and_forget():
    box = []
    filament.spawn_n(lambda: box.append("ran"))
    filament.sleep(0)  # let the scheduler run it
    filament.sleep(0)
    assert box == ["ran"]


def test_spawn_n_returns_none():
    assert filament.spawn_n(lambda: None) is None


def test_spawn_n_swallows_exception(capsys):
    # A raising spawn_n must NOT propagate to the caller (it prints instead).
    filament.spawn_n(lambda: 1 / 0)
    filament.sleep(0)
    filament.sleep(0)
    # We got here without an exception -- that is the assertion.  (The traceback
    # is printed to stderr; we don't require capturing it.)


def test_sleep0_fairness():
    # Two greenthreads alternating via sleep(0) should interleave.
    order = []

    def a():
        for _ in range(3):
            order.append("a")
            filament.sleep(0)

    def b():
        for _ in range(3):
            order.append("b")
            filament.sleep(0)

    ga = filament.spawn(a)
    gb = filament.spawn(b)
    filament.joinall([ga, gb])
    # Fair interleave: neither runs entirely before the other.
    assert order.count("a") == 3 and order.count("b") == 3
    # First two entries are from different greenthreads (they interleave).
    assert set(order[:2]) == set(["a", "b"])


def test_getcurrent_identity():
    box = []

    def capture():
        box.append(filament.getcurrent())

    g = filament.spawn(capture)
    g.wait()
    assert box[0] is g


def test_getcurrent_main_differs_from_spawned():
    main = filament.getcurrent()
    box = []
    filament.spawn(lambda: box.append(filament.getcurrent())).wait()
    assert box[0] is not main


def test_spawn_later_fires_after_delay():
    box = []
    handle = filament.spawn_later(0.02, lambda: box.append("fired"))
    # Not yet fired.
    assert box == []
    result = handle.wait()
    assert box == ["fired"]
    assert result is None or box == ["fired"]


def test_spawn_later_cancel_prevents_fire():
    box = []
    handle = filament.spawn_later(0.1, lambda: box.append("nope"))
    handle.cancel()
    filament.sleep(0.15)
    assert box == []


def test_spawn_after_is_alias():
    assert filament.spawn_after is filament.spawn_later


def test_spawn_later_wait_returns_value():
    handle = filament.spawn_later(0.01, lambda: 99)
    assert handle.wait() == 99


def test_kill_stops_greenthread():
    started = []

    def loop():
        started.append(True)
        while True:
            filament.sleep(0.001)

    g = filament.spawn(loop)
    filament.sleep(0)  # let it start
    assert started == [True]
    filament.kill(g)
    assert g.dead


def test_kill_self_raises_greenletexit():
    seen = []

    def suicide():
        try:
            filament.kill(filament.getcurrent())
        except filament.GreenletExit:
            seen.append("caught")
            raise

    g = filament.spawn(suicide)
    filament.joinall([g])
    assert seen == ["caught"]
    assert g.dead


def test_killall_stops_all():
    def loop():
        while True:
            filament.sleep(0.001)

    gs = [filament.spawn(loop) for _ in range(20)]
    filament.sleep(0)
    filament.killall(gs)
    assert all(g.dead for g in gs)


def test_joinall_with_timeout_returns_early():
    import time as _t
    def slow():
        filament.sleep(0.3)

    gs = [filament.spawn(slow) for _ in range(3)]
    t0 = _t.time()
    # joinall SHOULD return within ~timeout even though the greenthreads run
    # longer; it currently blocks for the full 0.3s (see xfail reason).
    filament.joinall(gs, timeout=0.05)
    elapsed = _t.time() - t0
    filament.killall(gs)
    assert elapsed < 0.2


def test_joinall_raise_error():
    def boom():
        raise KeyError("x")

    def ok():
        return 1

    gs = [filament.spawn(ok), filament.spawn(boom), filament.spawn(ok)]
    with pytest.raises(KeyError):
        filament.joinall(gs, raise_error=True)


def test_wait_and_iwait():
    gs = [filament.spawn(lambda i=i: i) for i in range(5)]
    done = filament.wait(gs)
    assert set(done) == set(gs)
    # iwait yields each object
    gs2 = [filament.spawn(lambda i=i: i) for i in range(5)]
    seen = list(filament.iwait(gs2))
    assert set(seen) == set(gs2)


def test_wait_count_limits():
    gs = [filament.spawn(lambda i=i: i) for i in range(10)]
    done = filament.wait(gs, count=3)
    assert len(done) == 3
    filament.joinall(gs)


def test_wait_none_returns_empty():
    assert filament.wait(None) == []
