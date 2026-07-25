# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Runtime-selectable debug mode.

With the vendored greenlet (Python 3 builds), the eager per-switch
introspection work (top-frame materialization + the 3.12+ expose_frames()
chain walk) is skipped by default and reconstructed lazily when a parked
greenthread's ``gr_frame`` is read.  ``filament.set_debug(True)`` restores
the classic fully-eager behavior (and sweeps already-parked greenthreads);
a per-thread trace/profile function auto-arms it per switch.

On classic-greenlet builds (py2.7/py3.8) ``set_debug``/``get_debug`` exist
but are behaviorally inert (classic greenlet is always eager); the tests
that inspect materialization state are skipped there.

GC invariants proven here IN BOTH MODES:
  * objects reachable only through a parked greenthread's frame locals
    survive collection (keepalive: no premature free / use-after-free);
  * reference cycles held only by a parked greenthread's frames are
    collected as soon as the greenthread finishes or is killed;
  * a cycle between the greenthread *object* and its own frame locals is
    intentionally NOT collected while the greenthread is alive-and-parked:
    upstream greenlet's ``green_is_gc`` exempts active greenlets from
    cycle collection by design (identical in eager mode and in classic
    greenlet), because collecting a suspended execution context that could
    still resume would be wrong.  It collects promptly once the
    greenthread is dead.
"""

from __future__ import absolute_import

import gc
import sys
import traceback
import weakref

import pytest

import filament

from tests._helpers import run_py

try:
    import _fil_greenlet
    VENDORED = True
except ImportError:  # py2.7 / py3.8 classic-greenlet builds
    _fil_greenlet = None
    VENDORED = False

# Lazy (runtime-selectable) frame materialization is implemented for the
# vendored greenlet on CPython 3.12/3.13 GIL builds (VGL_RUNTIME_LAZY).
# Elsewhere the vendored build stays fully eager (3.10: the top-frame save
# is mandatory state management; 3.14+: stackpointer capture needs the
# materialized frame at switch-out).
LAZY = (VENDORED
        and (3, 12) <= sys.version_info[:2] < (3, 14)
        and getattr(sys, "_is_gil_enabled", lambda: True)())

needs_vendored = pytest.mark.skipif(not VENDORED,
                                    reason="classic-greenlet build")
needs_lazy = pytest.mark.skipif(not LAZY,
                                reason="lazy debug mode needs vendored "
                                       "greenlet on CPython 3.12/3.13")


@pytest.fixture(autouse=True)
def _debug_off_guard():
    """Every test starts and ends with debug mode off."""
    filament.set_debug(False)
    yield
    filament.set_debug(False)


def _park_one(ev):
    """Spawn a greenthread that parks on ``ev`` inside a nested call."""
    def inner_wait():
        marker_local = "debug-mode-marker"  # noqa: F841 (visible via frame)
        ev.wait()
        return marker_local

    def body():
        return inner_wait()

    gt = filament.spawn(body)
    filament.sleep(0)  # let it run until it parks on the event
    return gt


def _materialized(gt):
    return _fil_greenlet._frame_materialized(gt)


# ---------------------------------------------------------------------------
# Flag plumbing (all builds)
# ---------------------------------------------------------------------------

def test_set_debug_toggle_roundtrip():
    assert filament.get_debug() is False
    filament.set_debug(True)
    assert filament.get_debug() is True
    filament.set_debug(False)
    assert filament.get_debug() is False
    # truthy/falsy values coerce
    filament.set_debug(1)
    assert filament.get_debug() is True
    filament.set_debug(0)
    assert filament.get_debug() is False


def test_filament_debug_env_honored_at_init():
    body = (
        "import filament\n"
        "assert filament.get_debug() is True, filament.get_debug()\n"
        "print('ENV_OK')\n"
    )
    res = run_py(body, extra_env={"FILAMENT_DEBUG": "1"})
    assert res.ok(), repr(res)
    assert "ENV_OK" in res.stdout

    body_off = (
        "import filament\n"
        "assert filament.get_debug() is False, filament.get_debug()\n"
        "print('ENV_OFF_OK')\n"
    )
    res = run_py(body_off, extra_env={"FILAMENT_DEBUG": "0"})
    assert res.ok(), repr(res)
    assert "ENV_OFF_OK" in res.stdout


# ---------------------------------------------------------------------------
# Lazy materialization (vendored 3.12/3.13)
# ---------------------------------------------------------------------------

@needs_lazy
def test_default_off_parked_frame_not_materialized():
    ev = filament.Event()
    gt = _park_one(ev)
    try:
        assert filament.get_debug() is False
        assert _materialized(gt) is False
    finally:
        ev.set()
        gt.wait()


@needs_lazy
def test_gr_frame_lazy_read_of_parked_greenthread():
    ev = filament.Event()
    gt = _park_one(ev)
    try:
        assert _materialized(gt) is False
        frame = gt.gr_frame
        assert frame is not None
        # postmortem-style usage: render the parked stack
        stack = "".join(traceback.format_stack(frame))
        assert "inner_wait" in stack
        assert "ev.wait()" in stack
        # walking f_back reaches the outer body frame
        names = []
        inner_frame = None
        f = frame
        while f is not None:
            names.append(f.f_code.co_name)
            if f.f_code.co_name == "inner_wait":
                inner_frame = f
            f = f.f_back
        assert "inner_wait" in names
        assert "body" in names
        # frame locals of the parked chain are readable
        assert inner_frame is not None
        assert inner_frame.f_locals.get("marker_local") == "debug-mode-marker"
        # the lazy read materialized (and cached) the top frame
        assert _materialized(gt) is True
    finally:
        ev.set()
        assert gt.wait() == "debug-mode-marker"


@needs_lazy
def test_set_debug_sweeps_already_parked_greenthreads():
    ev = filament.Event()
    gts = [_park_one(ev) for _ in range(3)]
    try:
        for gt in gts:
            assert _materialized(gt) is False
        filament.set_debug(True)
        # the sweep materialized frames of pre-existing parked fibers
        # without anybody reading gr_frame
        for gt in gts:
            assert _materialized(gt) is True
    finally:
        filament.set_debug(False)
        ev.set()
        for gt in gts:
            gt.wait()


@needs_lazy
def test_debug_on_parks_are_eager():
    filament.set_debug(True)
    ev = filament.Event()
    gt = _park_one(ev)
    try:
        assert _materialized(gt) is True
        assert gt.gr_frame is not None
    finally:
        ev.set()
        gt.wait()
        filament.set_debug(False)


@needs_lazy
def test_auto_arm_via_settrace():
    ev = filament.Event()

    def tracer(frame, event, arg):  # pragma: no cover - side effect only
        return None

    sys.settrace(tracer)
    try:
        gt = _park_one(ev)
        # debug flag itself is still off...
        assert filament.get_debug() is False
        # ...but the switch that parked gt saw a c_tracefunc and armed
        assert _materialized(gt) is True
    finally:
        sys.settrace(None)
        ev.set()
        gt.wait()


@needs_lazy
def test_mode_toggles_across_park_resume_cycles():
    # exercise expose/unexpose pairing when the flag flips while parked
    ev = filament.Event()
    hops = []

    def body():
        for i in range(6):
            hops.append(i)
            filament.sleep(0)
        ev.wait()
        return sum(hops)

    gt = filament.spawn(body)
    for i in range(6):
        filament.set_debug(i % 2 == 0)
        filament.sleep(0)
    filament.set_debug(False)
    filament.sleep(0)
    # parked on the event now with debug off; read (materializes), then
    # resume (unexposes), park again, and verify it re-parks lazily
    assert gt.gr_frame is not None
    assert _materialized(gt) is True
    ev.set()
    assert gt.wait() == sum(range(6))


# ---------------------------------------------------------------------------
# GC invariants (both modes; all builds)
# ---------------------------------------------------------------------------

class _Box(object):
    pass


def _gc_frame_cycle_scenario():
    """Cycle held only by a parked greenthread's frame local."""
    refs = {}
    ev = filament.Event()

    def body():
        a = _Box()
        b = _Box()
        a.other = b
        b.other = a
        refs["a"] = weakref.ref(a)
        refs["b"] = weakref.ref(b)
        ev.wait()
        # keepalive check on resume: the cycle must be fully intact
        assert a.other is b and b.other is a
        return "done"

    gt = filament.spawn(body)
    filament.sleep(0)

    gc.collect()
    gc.collect()
    parked_alive = (refs["a"]() is not None) and (refs["b"]() is not None)

    ev.set()
    assert gt.wait() == "done"
    del gt
    gc.collect()
    collected_after = (refs["a"]() is None) and (refs["b"]() is None)
    return parked_alive, collected_after


@pytest.mark.parametrize("debug", [False, True])
def test_gc_cycle_through_parked_frames_collects(debug):
    filament.set_debug(debug)
    try:
        parked_alive, collected_after = _gc_frame_cycle_scenario()
    finally:
        filament.set_debug(False)
    # while parked, the cycle is live program state (frame locals) and
    # must NOT be collected -- this is the memory-safety direction
    assert parked_alive is True
    # once the greenthread finished, the cycle must collect
    assert collected_after is True


@pytest.mark.parametrize("debug", [False, True])
def test_gc_cycle_involving_greenthread_object(debug):
    filament.set_debug(debug)
    refs = {}
    ev = filament.Event()

    def body():
        box = _Box()
        box.me = filament.getcurrent()  # frame local -> box -> greenthread
        refs["box"] = weakref.ref(box)
        ev.wait()
        return "ok"

    try:
        gt = filament.spawn(body)
        refs["gt"] = weakref.ref(gt)
        filament.sleep(0)

        gc.collect()
        # Parity with eager/classic greenlet: an alive-and-parked
        # greenthread is exempt from cycle collection (green_is_gc), so
        # the cycle survives -- and its state stays fully valid.
        assert refs["box"]() is not None
        assert gt.gr_frame is not None

        ev.set()
        assert gt.wait() == "ok"
        del gt
        gc.collect()
        # dead greenthread: the cycle collapses
        assert refs["box"]() is None
        assert refs["gt"]() is None
    finally:
        filament.set_debug(False)


@pytest.mark.parametrize("debug", [False, True])
def test_gc_killed_parked_greenthread_releases_frame_refs(debug):
    filament.set_debug(debug)
    refs = {}
    ev = filament.Event()

    def body():
        keep = _Box()
        keep.self_cycle = keep
        refs["keep"] = weakref.ref(keep)
        ev.wait()

    try:
        gt = filament.spawn(body)
        filament.sleep(0)
        gc.collect()
        assert refs["keep"]() is not None  # parked => alive
        filament.kill(gt)  # includes a sleep(0): the throw runs now
        assert gt.dead
        del gt
        gc.collect()
        assert refs["keep"]() is None  # killed => frame refs released
    finally:
        filament.set_debug(False)
