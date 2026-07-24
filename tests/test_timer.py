# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Tests for the C scheduler ``Timer`` primitive (``_filament.timer.Timer``).

A Timer schedules a callback on the current thread's scheduler after a delay;
``.cancel()`` disarms it before it fires.  These underpin Timeout, spawn_later
and kill, so we exercise them directly here.
"""

from __future__ import absolute_import

import filament
from _filament.timer import Timer


def run(fn):
    return filament.spawn(fn).wait()


def test_timer_fires_after_delay():
    def body():
        fired = []
        Timer(0.02, lambda: fired.append("fired"))
        assert fired == []
        filament.sleep(0.05)
        return fired

    assert run(body) == ["fired"]


def test_timer_cancel_prevents_fire():
    def body():
        fired = []
        t = Timer(0.05, lambda: fired.append("nope"))
        t.cancel()
        filament.sleep(0.1)
        return fired

    assert run(body) == []


def test_timer_passes_args():
    def body():
        out = []
        Timer(0.01, lambda a, b: out.append((a, b)), "x", "y")
        filament.sleep(0.03)
        return out

    assert run(body) == [("x", "y")]


def test_zero_delay_timer_fires_on_next_turn():
    def body():
        fired = []
        Timer(0, lambda: fired.append(True))
        filament.sleep(0)
        return fired

    assert run(body) == [True]


def test_multiple_timers_all_fire():
    def body():
        fired = []
        for i in range(10):
            Timer(0.001 * i, lambda i=i: fired.append(i))
        filament.sleep(0.05)
        return sorted(fired)

    assert run(body) == list(range(10))
