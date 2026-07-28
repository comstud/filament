"""Scheduler queue microbenchmarks: timer arming, cancellation, raw switching.

Run with PYTHONPATH=/workspace so it measures the working tree:

    PYTHONPATH=/workspace .venvs/py313/bin/python gevbench/timerbench.py

The interesting axis is how the cost of arming a timeout scales with the
number of timers already waiting -- a sorted linked list makes that a linear
walk, a heap makes it logarithmic -- plus whether cancelling one actually
gives the memory back.
"""

from __future__ import print_function

import gc
import time

import filament


def _rss_kb():
    gc.collect()
    # Current RSS: ru_maxrss is a high-water mark and would hide memory that
    # was actually released.
    with open('/proc/self/statm') as fh:
        return int(fh.read().split()[1]) * 4


def _time_op(op, n):
    start = time.time()
    for _ in range(n):
        op()
    return (time.time() - start) / n * 1e6         # microseconds per op


def arm_cost_with_pending(n_pending, live, n=2000):
    """Cost of arming+cancelling a timeout with n_pending others waiting.

    ``live`` picks what those others are: timers still armed (they exercise
    the queue's insert path), or timers that were cancelled (which used to
    stay in the queue until their deadline).
    """
    keep = [filament.Timeout(3600) for _ in range(n_pending)]
    for timeout in keep:
        timeout.start()
        if not live:
            timeout.cancel()

    def op():
        timeout = filament.Timeout(30)
        timeout.start()
        timeout.cancel()

    try:
        return _time_op(op, n)
    finally:
        for timeout in keep:
            timeout.cancel()


def switch_cost(n=20000):
    """The immediate path: sleep(0) is one queued event per switch."""
    return _time_op(lambda: filament.sleep(0), n)


def sleep_wakeup_cost(n=2000):
    """A real (tiny) sleep: queue a timer, wait for it to fire."""
    return _time_op(lambda: filament.sleep(0.0005), n) - 500.0


def cancel_leak(n=20000):
    """Memory retained by n armed-then-cancelled 30s timeouts."""
    for _ in range(1000):
        timeout = filament.Timeout(30)
        timeout.start()
        timeout.cancel()
    before = _rss_kb()
    for _ in range(n):
        timeout = filament.Timeout(30)
        timeout.start()
        timeout.cancel()
    return _rss_kb() - before


def main():
    print("arming a timeout, with N timers already ARMED:")
    for pending in (0, 1000, 10000, 50000):
        print("  %6d pending -> %7.2f us" % (pending, arm_cost_with_pending(pending, True)))

    print("arming a timeout, with N timers armed then CANCELLED:")
    for pending in (0, 1000, 10000, 50000):
        print("  %6d cancelled -> %7.2f us" % (pending, arm_cost_with_pending(pending, False)))

    print("hot paths:")
    print("  sleep(0) switch      -> %7.3f us" % switch_cost())
    print("  sleep(0.0005) overhead -> %7.1f us over the requested time"
          % sleep_wakeup_cost())
    print("memory:")
    print("  20000 x armed+cancelled 30s timeouts -> %+.1f KB retained" % cancel_leak())


if __name__ == '__main__':
    main()
