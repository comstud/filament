# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
tpool tests: run blocking work on real OS threads while other greenthreads keep
running.

The load-bearing property (and the reason tpool exists) is that a blocking call
routed through ``tpool.execute`` must NOT freeze the cooperative scheduler:
other greenthreads make progress while the worker thread blocks.
"""

from __future__ import absolute_import

import time as _real_time

import pytest

import filament
import filament.tpool as tpool


def test_execute_returns_value():
    def work(a, b):
        return a + b

    assert filament.spawn(lambda: tpool.execute(work, 3, 4)).wait() == 7


def test_execute_passes_kwargs():
    def work(a, b=0):
        return a * b

    assert filament.spawn(lambda: tpool.execute(work, 6, b=7)).wait() == 42


def test_execute_reraises_exception():
    def boom():
        raise KeyError("nope")

    with pytest.raises(KeyError):
        filament.spawn(lambda: tpool.execute(boom)).wait()


def test_blocking_worker_does_not_block_scheduler():
    # While a real-thread blocking sleep runs, a separate greenthread must keep
    # incrementing a counter -- proving the scheduler was not frozen.
    counter = [0]

    def busy():
        for _ in range(200):
            counter[0] += 1
            filament.sleep(0.001)

    def driver():
        g = filament.spawn(busy)

        def blocker():
            _real_time.sleep(0.15)   # genuine blocking call on a worker thread
            return "blocked-done"

        result = tpool.execute(blocker)
        # The busy greenthread should have advanced meaningfully during the
        # 0.15s the worker thread was blocked.
        progressed = counter[0]
        g.wait()
        return result, progressed

    result, progressed = filament.spawn(driver).wait()
    assert result == "blocked-done"
    assert progressed > 0


def test_concurrent_executes():
    # Several greenthreads each offload a blocking call concurrently.
    def blocker(x):
        _real_time.sleep(0.05)
        return x * 10

    def driver():
        gts = [filament.spawn(lambda x=x: tpool.execute(blocker, x))
               for x in range(8)]
        return [g.wait() for g in gts]

    assert filament.spawn(driver).wait() == [x * 10 for x in range(8)]


def test_proxy_dispatches_method_calls():
    class Adder(object):
        def __init__(self, base):
            self.base = base

        def add(self, x):
            _real_time.sleep(0.01)
            return self.base + x

    def driver():
        proxy = tpool.Proxy(Adder(100))
        return proxy.add(5)

    assert filament.spawn(driver).wait() == 105


def test_proxy_attribute_read():
    class Holder(object):
        value = 42

    def driver():
        return tpool.Proxy(Holder()).value

    assert filament.spawn(driver).wait() == 42


def test_set_num_threads():
    # Resizing the default pool should not break subsequent executes.
    def driver():
        tpool.set_num_threads(2)
        return tpool.execute(lambda: "ok")

    assert filament.spawn(driver).wait() == "ok"
