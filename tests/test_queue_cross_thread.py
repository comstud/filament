# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Cross-domain queue tests: ONE filament Queue shared between greenthreads
and native OS threads at the same time.

gevent/eventlet queues are hub-bound -- using them from a foreign OS
thread is undefined behaviour (deadlock or ``cannot switch to a different
thread``).  filament's waiters bind (scheduler, greenlet) at wait time and
cross-thread signals are deferred onto the waiter's home scheduler, so a
native thread blocking in ``q.get()``/``q.put()`` while greenthreads work
the same queue is a fully supported pattern.  These tests pin that down.
"""

from __future__ import absolute_import

import threading

import filament
from filament import queue as filq


JOIN_TIMEOUT = 30


def _join_all(threads):
    for t in threads:
        t.join(JOIN_TIMEOUT)
    assert not any(t.is_alive() for t in threads), \
        "native thread(s) hung on shared queue"


def _native(fn, *args):
    t = threading.Thread(target=fn, args=args)
    t.daemon = True
    t.start()
    return t


def test_green_producer_native_consumer():
    q = filq.Queue()
    got = []

    def native_consumer():
        for _ in range(1000):
            got.append(q.get())

    t = _native(native_consumer)

    def green_producer():
        for i in range(1000):
            q.put(i)

    filament.wait([filament.spawn(green_producer)])
    _join_all([t])
    assert got == list(range(1000))


def test_native_producer_green_consumer_bounded():
    # maxsize forces the NATIVE producer to block in q.put() on a full
    # queue and be woken by a greenthread's q.get().
    q = filq.Queue(maxsize=10)
    got = []

    def native_producer():
        for i in range(1000):
            q.put(i)

    t = _native(native_producer)

    def green_consumer():
        for _ in range(1000):
            got.append(q.get())

    filament.wait([filament.spawn(green_consumer)])
    _join_all([t])
    assert got == list(range(1000))


def test_fully_mixed_single_queue():
    # Green + native producers AND green + native consumers, all on one
    # bounded queue simultaneously.
    n = 500
    q = filq.Queue(maxsize=50)
    got = []
    got_lock = threading.Lock()

    def producer(base):
        for i in range(n):
            q.put(base + i)

    def consumer():
        for _ in range(n):
            v = q.get()
            with got_lock:
                got.append(v)

    native = [_native(producer, 100000), _native(consumer)]
    filament.wait([filament.spawn(producer, 0),
                   filament.spawn(consumer)])
    _join_all(native)
    assert sorted(got) == sorted(list(range(n)) +
                                 list(range(100000, 100000 + n)))


def test_native_thread_get_timeout():
    # Timed q.get() from a native thread must raise Empty, not hang.
    q = filq.Queue()
    outcome = {}

    def native_getter():
        try:
            q.get(timeout=0.05)
            outcome["result"] = "got-value"
        except filq.Empty:
            outcome["result"] = "empty"
        except Exception as e:  # pragma: no cover - diagnostic
            outcome["result"] = "error: %r" % (e,)

    t = _native(native_getter)
    _join_all([t])
    assert outcome.get("result") == "empty"


def test_tpool_workers_share_queue_with_greenthreads():
    # Same pattern via filament's OWN thread pool instead of raw
    # threading.Thread: tpool workers block in q.get() while greenthreads
    # produce.  (tpool.execute round-trips through real pool threads.)
    import filament.tpool as tpool

    q = filq.Queue(maxsize=20)
    n = 200

    def drain():
        total = 0
        for _ in range(n):
            total += q.get()
        return total

    def body():
        gp = filament.spawn(lambda: [q.put(1) for _ in range(n)])
        total = tpool.execute(drain)
        filament.wait([gp])
        return total

    assert filament.spawn(body).wait() == n
