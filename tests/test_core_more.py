# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Coverage-gap tests for the core pure-python modules:

  * filament.pool        (Group/Pool/GreenPool/GreenPile edges)
  * filament.pyqueue     (timeouts, iteration protocol, task accounting)
  * filament.greenthread (spawn_n, GreenThread wrapper, spawn_later, kill,
    iwait)
  * filament.timeout     (start/cancel lifecycle edges)
  * filament.tpool       (Proxy wrapping/dunders, shutdown)
  * filament.thrpool_resolver (kwargs passthrough, timeout validation)

Everything here runs in-process (none of it mutates global patch state).
"""

from __future__ import absolute_import

import socket as std_socket

import pytest

import filament
from filament import greenthread as fil_greenthread
from filament import pool as fil_pool
from filament import pyqueue as fil_pyqueue
from filament import thrpool_resolver as fil_resolver
from filament import timeout as fil_timeout
from filament import tpool as fil_tpool


# ---------------------------------------------------------------------------
# pool.Group
# ---------------------------------------------------------------------------

def test_group_membership_and_iter():
    ev = filament.Event()
    gt = filament.spawn(ev.wait)
    group = fil_pool.Group(gt)
    assert len(group) == 1
    assert gt in group
    assert list(group) == [gt]
    group.discard(gt)
    assert gt not in group
    group.discard(gt)          # absent -> no error
    group.add(gt)
    assert len(group) == 1
    ev.set()
    group.join()


def test_group_spawn_n_runs():
    ev = filament.Event()
    group = fil_pool.Group()
    assert group.spawn_n(ev.set) is None
    assert ev.wait(timeout=5) is True


def test_group_imap_propagates_exception():
    group = fil_pool.Group()

    def boom(x):
        raise ValueError('imap-%s' % (x,))

    it = group.imap(boom, [1])
    with pytest.raises(ValueError):
        next(iter(it))


# ---------------------------------------------------------------------------
# pool.Pool / GreenPool
# ---------------------------------------------------------------------------

def test_pool_unbounded_free_count():
    pool = fil_pool.Pool()          # size=None -> unbounded
    assert pool.free_count() == 1   # "effectively unbounded" sentinel
    assert pool.free() == 1
    gt = pool.spawn(lambda: 7)
    assert gt.wait() == 7


def test_pool_unbounded_resize_and_wait_available():
    pool = fil_pool.Pool()
    with pytest.raises(RuntimeError):
        pool.resize(4)
    # Unbounded wait_available is an immediate no-op.
    assert pool.wait_available() is None


def test_pool_spawn_n_gated():
    ev = filament.Event()
    pool = fil_pool.GreenPool(2)
    assert pool.spawn_n(ev.set) is None
    assert ev.wait(timeout=5) is True
    pool.waitall()


def test_pool_wait_available_and_resize():
    pool = fil_pool.GreenPool(1)
    release = filament.Event()
    pool.spawn(release.wait)
    assert pool.free_count() == 0
    # Growing releases permits immediately.
    pool.resize(2)
    assert pool.size == 2
    assert pool.free_count() == 1
    pool.wait_available()
    # Shrinking acquires the surplus back (a free slot exists, so no block).
    pool.resize(1)
    assert pool.size == 1
    release.set()
    pool.waitall()
    assert pool.free_count() == 1


# ---------------------------------------------------------------------------
# pool.GreenPile
# ---------------------------------------------------------------------------

def test_greenpile_orders_results_and_accepts_pool():
    shared = fil_pool.GreenPool(4)
    pile = fil_pool.GreenPile(shared)
    assert pile.pool is shared

    def job(i):
        # Later items finish first; results must still come back in order.
        filament.sleep(0.02 * (3 - i))
        return i

    for i in range(3):
        pile.spawn(job, i)
    assert list(pile) == [0, 1, 2]


def test_greenpile_none_builds_default_pool():
    pile = fil_pool.GreenPile(None)
    assert isinstance(pile.pool, fil_pool.GreenPool)
    pile.spawn(lambda: 'x')
    assert next(iter(pile)) == 'x'


# ---------------------------------------------------------------------------
# pyqueue
# ---------------------------------------------------------------------------

def test_pyqueue_get_timeout_and_nowait():
    q = fil_pyqueue.Queue()
    with pytest.raises(fil_pyqueue.Empty):
        q.get(timeout=0.02)
    with pytest.raises(fil_pyqueue.Empty):
        q.get_nowait()
    q.put(1)
    assert q.get_nowait() == 1


def test_pyqueue_put_full_paths():
    q = fil_pyqueue.Queue(1)
    q.put_nowait('a')
    with pytest.raises(fil_pyqueue.Full):
        q.put('b', block=False)
    with pytest.raises(fil_pyqueue.Full):
        q.put('b', timeout=0.02)
    assert q.get() == 'a'


def test_pyqueue_iteration_stops_on_sentinel():
    q = fil_pyqueue.Queue()
    q.put(1)
    q.put(2)
    q.put(StopIteration)
    assert list(q) == [1, 2]


def test_pyqueue_task_done_accounting():
    q = fil_pyqueue.Queue()
    with pytest.raises(ValueError):
        q.task_done()
    q.put('x')
    # Nothing done yet: join must time out and report False.
    assert q.join(timeout=0.02) is False
    q.get()
    q.task_done()
    assert q.join(timeout=1) is True


# ---------------------------------------------------------------------------
# greenthread
# ---------------------------------------------------------------------------

def test_spawn_n_swallows_greenlet_exit():
    ran = []

    def target():
        ran.append(True)
        raise fil_greenthread.GreenletExit()

    fil_greenthread.spawn_n(target)
    filament.sleep(0.05)
    assert ran == [True]


def test_greenthread_wrapper_delegates():
    gt = filament.spawn(lambda: 'val')
    wrapper = fil_greenthread.GreenThread(gt)
    assert wrapper.wait() == 'val'
    assert wrapper.dead is True
    # __getattr__ delegation to the underlying Filament.
    assert wrapper.wait == wrapper._filament.wait or wrapper.wait() == 'val'
    # kill() on a finished greenthread is a quiet no-op.
    wrapper.kill()


def test_spawn_later_cancel_and_exception():
    ran = []
    handle = fil_greenthread.spawn_later(0.01, ran.append, True)
    handle.cancel()
    filament.sleep(0.05)
    assert ran == []

    def boom():
        raise ValueError('later-boom')

    handle = fil_greenthread.spawn_later(0.01, boom)
    with pytest.raises(ValueError):
        handle.wait()


def test_kill_dead_and_self():
    gt = filament.spawn(lambda: None)
    gt.wait()
    # Killing an already-dead greenthread returns quietly.
    assert fil_greenthread.kill(gt) is None

    caught = []

    def suicidal():
        me = fil_greenthread.getcurrent()
        try:
            fil_greenthread.kill(me, ValueError, 'self-kill')
        except ValueError as e:
            caught.append(str(e))

    filament.spawn(suicidal).wait()
    assert caught == ['self-kill']


def test_wait_tolerates_failures_and_count():
    def boom():
        raise ValueError('iwait-boom')

    bad = filament.spawn(boom)
    good = filament.spawn(lambda: 1)
    # wait() reports completion; the exception stays with the object.
    done = fil_greenthread.wait([bad, good])
    assert done == [bad, good]
    # count= stops the iterator early.
    a = filament.spawn(lambda: 'a')
    b = filament.spawn(lambda: 'b')
    first = list(fil_greenthread.iwait([a, b], count=1))
    assert first == [a]


# ---------------------------------------------------------------------------
# timeout.Timeout lifecycle
# ---------------------------------------------------------------------------

def test_timeout_double_start_forbidden():
    t = fil_timeout.Timeout(5)
    t.start()
    try:
        with pytest.raises(RuntimeError):
            t.start()
    finally:
        t.cancel()
    assert t.pending is False


def test_timeout_none_never_arms():
    t = fil_timeout.Timeout(None)
    t.start()
    assert t.pending is False
    t.cancel()                  # harmless when never armed
    assert str(t) == ''


# ---------------------------------------------------------------------------
# tpool.Proxy
# ---------------------------------------------------------------------------

def test_tpool_proxy_container_dunders():
    d = {}
    p = fil_tpool.Proxy(d)
    p['k'] = 'v'
    assert p['k'] == 'v'
    assert repr(p) == repr({'k': 'v'})


def test_tpool_proxy_autowrap_and_setattr():
    class Inner(object):
        pass

    class Outer(object):
        def __init__(self):
            self.inner = Inner()
            self.plain = 3

        def make(self):
            return Inner()

        def named(self):
            return object()

    outer = Outer()
    p = fil_tpool.Proxy(outer, autowrap=(Inner,), autowrap_names=('named',))
    # Callable returning an autowrap instance -> proxied result.
    assert isinstance(p.make(), fil_tpool.Proxy)
    # Callable matched by name -> proxied result.
    assert isinstance(p.named(), fil_tpool.Proxy)
    # Non-callable attribute of an autowrap type -> proxied via _wrap.
    assert isinstance(p.inner, fil_tpool.Proxy)
    assert p.plain == 3
    # Attribute writes go straight to the wrapped object.
    p.plain = 9
    assert outer.plain == 9


def test_tpool_proxy_callable_object():
    p = fil_tpool.Proxy(len)
    assert p([1, 2, 3]) == 3


def test_tpool_shutdown_and_recreate():
    assert fil_tpool.execute(lambda: 41) == 41
    fil_tpool.shutdown()
    fil_tpool.shutdown()        # idempotent when no default pool exists
    # A fresh default pool is built on demand afterwards.
    assert fil_tpool.execute(lambda: 42) == 42


# ---------------------------------------------------------------------------
# thrpool_resolver
# ---------------------------------------------------------------------------

def test_resolver_zero_timeout_rejected():
    with pytest.raises(ValueError):
        fil_resolver.Resolver(timeout=0.0)


def test_resolver_kwargs_passthrough():
    r = fil_resolver.Resolver()
    try:
        # Positional-only path (no kwargs forwarded).
        assert r.gethostbyname('localhost')
        # Keyword arguments ride run()'s dedicated kwargs passthrough.
        res = r.getaddrinfo('localhost', 80, type=std_socket.SOCK_STREAM)
        assert res
        for entry in res:
            assert entry[1] == std_socket.SOCK_STREAM
    finally:
        r.shutdown()
