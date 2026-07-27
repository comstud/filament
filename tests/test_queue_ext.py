# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Extended queue tests: cooperative blocking put/get across greenthreads,
Empty/Full timeouts, task_done/join, SimpleQueue, and the pyqueue
Lifo/Priority disciplines.
"""

from __future__ import absolute_import

import pytest

import filament
from _filament import queue as cqueue
from filament import pyqueue


def run(fn):
    return filament.spawn(fn).wait()


# --------------------------------------------------------------------------- #
# Blocking hand-off across greenthreads
# --------------------------------------------------------------------------- #

def test_blocking_get_waits_for_put():
    def body():
        q = cqueue.Queue()
        got = []

        def consumer():
            got.append(q.get())      # blocks until producer puts

        def producer():
            filament.sleep(0.01)
            q.put("hello")

        filament.joinall([filament.spawn(consumer), filament.spawn(producer)])
        return got

    assert run(body) == ["hello"]


def test_blocking_put_waits_on_full_queue():
    def body():
        q = cqueue.Queue(maxsize=1)
        q.put(1)                     # fill it
        order = []

        def producer():
            order.append("put-start")
            q.put(2)                 # blocks until consumer drains
            order.append("put-done")

        def consumer():
            filament.sleep(0.01)
            order.append(("got", q.get()))
            order.append(("got", q.get()))

        filament.joinall([filament.spawn(producer), filament.spawn(consumer)])
        return order

    order = run(body)
    # The producer must observe put-start before it blocks; both items are
    # eventually delivered, and put-done only appears after the slot freed
    # (i.e. after at least the first get).
    assert order[0] == "put-start"
    assert ("got", 1) in order and ("got", 2) in order
    assert "put-done" in order
    assert order.index("put-done") > order.index(("got", 1))


def test_get_timeout_raises_empty():
    def body():
        q = cqueue.Queue()
        with pytest.raises(cqueue.Empty):
            q.get(timeout=0.02)
    run(body)


def test_put_timeout_raises_full():
    def body():
        q = cqueue.Queue(maxsize=1)
        q.put(1)
        with pytest.raises(cqueue.Full):
            q.put(2, timeout=0.02)
    run(body)


def test_get_nowait_empty_raises():
    def body():
        q = cqueue.Queue()
        with pytest.raises(cqueue.Empty):
            q.get_nowait()
    run(body)


def test_put_nowait_full_raises():
    def body():
        q = cqueue.Queue(maxsize=1)
        q.put_nowait(1)
        with pytest.raises(cqueue.Full):
            q.put_nowait(2)
    run(body)


# --------------------------------------------------------------------------- #
# task_done / join
# --------------------------------------------------------------------------- #

def test_task_done_and_join():
    def body():
        q = cqueue.Queue()
        for i in range(5):
            q.put(i)
        joined = []

        def waiter():
            q.join()                 # blocks until all task_done() called
            joined.append(True)

        def worker():
            while True:
                try:
                    q.get_nowait()
                except cqueue.Empty:
                    break
                filament.sleep(0)
                q.task_done()

        gw = filament.spawn(waiter)
        filament.sleep(0)
        assert joined == []          # not done yet
        filament.spawn(worker).wait()
        gw.wait()
        return joined

    assert run(body) == [True]


# --------------------------------------------------------------------------- #
# many producers / consumers
# --------------------------------------------------------------------------- #

def test_many_producers_consumers():
    def body():
        q = cqueue.Queue()
        n = 200
        consumed = []

        def producer(base):
            for i in range(base, base + 50):
                q.put(i)

        def consumer():
            while len(consumed) < n:
                consumed.append(q.get())

        producers = [filament.spawn(producer, b) for b in (0, 50, 100, 150)]
        cons = filament.spawn(consumer)
        filament.joinall(producers)
        cons.wait()
        return sorted(consumed)

    assert run(body) == list(range(200))


# --------------------------------------------------------------------------- #
# SimpleQueue
# --------------------------------------------------------------------------- #

def test_simplequeue_fifo():
    def body():
        q = cqueue.SimpleQueue()
        for i in range(5):
            q.put(i)
        return [q.get() for _ in range(5)]

    assert run(body) == [0, 1, 2, 3, 4]


def test_simplequeue_blocking():
    def body():
        q = cqueue.SimpleQueue()
        got = []
        filament.spawn_n(lambda: (filament.sleep(0.01), q.put("x")))
        filament.spawn(lambda: got.append(q.get())).wait()
        return got

    assert run(body) == ["x"]


# --------------------------------------------------------------------------- #
# pyqueue disciplines
# --------------------------------------------------------------------------- #

def test_pyqueue_fifo():
    def body():
        q = pyqueue.Queue()
        for i in range(5):
            q.put(i)
        return [q.get() for _ in range(5)]

    assert run(body) == [0, 1, 2, 3, 4]


def test_pyqueue_lifo():
    def body():
        q = pyqueue.LifoQueue()
        for i in range(5):
            q.put(i)
        return [q.get() for _ in range(5)]

    assert run(body) == [4, 3, 2, 1, 0]


def test_pyqueue_priority():
    def body():
        q = pyqueue.PriorityQueue()
        for x in (5, 1, 4, 2, 3):
            q.put(x)
        return [q.get() for _ in range(5)]

    assert run(body) == [1, 2, 3, 4, 5]


def test_pyqueue_lite():
    def body():
        q = pyqueue.LiteQueue()
        q.put("a")
        q.put("b")
        return [q.get(), q.get()]

    assert run(body) == ["a", "b"]


def test_pyqueue_priority_blocking_across_greenthreads():
    def body():
        q = pyqueue.PriorityQueue()
        got = []

        def consumer():
            got.append(q.get())
            got.append(q.get())

        def producer():
            filament.sleep(0.01)
            q.put(10)
            q.put(1)

        filament.joinall([filament.spawn(consumer), filament.spawn(producer)])
        return got

    # Consumer parks on the first get until an item exists; ordering within the
    # queue is by priority once both are present.
    assert sorted(run(body)) == [1, 10]


# --------------------------------------------------------------------------- #
# Killed while an item / a slot is being handed over
#
# put() wakes exactly one parked getter, and get() wakes exactly one parked
# putter.  If that greenthread is thrown into (kill(), an expiring Timeout) in
# the same wakeup, it cannot use what it was woken for -- so the wakeup has to
# be passed on, or the next waiter sleeps through a queue that is no longer
# empty (or no longer full).
# --------------------------------------------------------------------------- #

def _queue_throw(g):
    """Queue a throw into `g` WITHOUT yielding (kill() yields)."""
    from filament.greenthread import GreenletExit
    from filament.timer import Timer

    Timer(0, g.throw, GreenletExit)


def test_get_wakeup_passed_on_when_getter_is_killed():
    def body():
        q = cqueue.Queue()
        got = []

        v = filament.spawn(lambda: got.append(("victim", q.get())))
        s = filament.spawn(lambda: got.append(("survivor", q.get())))
        filament.sleep(0)              # both parked in get()

        _queue_throw(v)                # the victim is on its way out ...
        q.put("item")                  # ... and put() wakes exactly one getter

        filament.sleep(0.05)
        assert v.dead
        with filament.Timeout(1.0):
            s.wait()
        return got, q.qsize()

    got, qsize = run(body)
    assert got == [("survivor", "item")], got
    assert qsize == 0


def test_put_wakeup_passed_on_when_putter_is_killed():
    def body():
        q = cqueue.Queue(maxsize=1)
        q.put("first")                 # queue is now full
        done = []

        v = filament.spawn(lambda: (q.put("victim"), done.append("victim")))
        s = filament.spawn(lambda: (q.put("survivor"), done.append("survivor")))
        filament.sleep(0)              # both parked in put()

        _queue_throw(v)
        assert q.get() == "first"      # makes room, wakes exactly one putter

        filament.sleep(0.05)
        assert v.dead
        with filament.Timeout(1.0):
            s.wait()
        return done, q.get()

    done, item = run(body)
    assert done == ["survivor"], done
    assert item == "survivor"
