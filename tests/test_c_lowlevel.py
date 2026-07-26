# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Tests aimed at C-extension entry points the rest of the suite never reaches
(mapped via gcov):

  * ``_filament.socket``: the UDP surface (sendto/recvfrom/recvfrom_into and
    their io-thread processors), py2-style ``accept()``, ``connect_ex``,
    ``getpeername``, ``dup``, ``shutdown``, ``setblocking``, construction from
    an existing fileno, and the resolver-control module functions.
  * ``_filament.queue``: Queue qsize/empty/full and the *_nowait fast paths;
    SimpleQueue qsize/len/get_nowait/put_nowait.
  * ``_filament.core``: direct ``Filament(...)`` construction, Scheduler's
    greenlet()/switch() methods, and the pthread TSD destructor that fires
    when a real OS thread with a scheduler exits.

NB: always ``import filament`` before touching ``_filament.*`` (importing the
C modules first can crash the interpreter).
"""

from __future__ import absolute_import

import socket as std_socket
import threading as std_threading

import pytest

import filament
from filament import exc as fil_exc

import _filament.core as _core
import _filament.queue as _cqueue
import _filament.socket as _csocket

from tests._helpers import run_py


def _udp_pair():
    r = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_DGRAM)
    w = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_DGRAM)
    r.bind(('127.0.0.1', 0))
    return r, w, r.getsockname()


# ---------------------------------------------------------------------------
# UDP: sendto / recvfrom / recvfrom_into
# ---------------------------------------------------------------------------

def test_udp_sendto_recvfrom_immediate():
    r, w, addr = _udp_pair()
    try:
        assert w.sendto(b'ping', addr) == 4
        data, peer = r.recvfrom(16)
        assert data == b'ping'
        assert peer[0] == '127.0.0.1'
    finally:
        r.close()
        w.close()


def test_udp_recvfrom_parked_then_woken():
    # The reader parks in the io thread first; the datagram arrives later, so
    # the wait-and-retry (processor) path runs rather than the fast path.
    r, w, addr = _udp_pair()
    try:
        reader = filament.spawn(r.recvfrom, 16)
        filament.sleep(0.05)           # let the reader park
        w.sendto(b'later', addr)
        data, peer = reader.wait()
        assert data == b'later'
    finally:
        r.close()
        w.close()


def test_udp_recvfrom_into():
    r, w, addr = _udp_pair()
    try:
        w.sendto(b'pong', addr)
        buf = bytearray(16)
        nbytes, peer = r.recvfrom_into(buf, 16)
        assert nbytes == 4
        assert bytes(buf[:4]) == b'pong'
        assert peer[0] == '127.0.0.1'
        # Parked variant.
        reader = filament.spawn(r.recvfrom_into, buf, 16)
        filament.sleep(0.05)
        w.sendto(b'zz', addr)
        nbytes, _peer = reader.wait()
        assert nbytes == 2
    finally:
        r.close()
        w.close()


def test_udp_recvfrom_timeout():
    r, w, addr = _udp_pair()
    try:
        r.settimeout(0.05)
        try:
            r.recvfrom(16)
        except fil_exc.Timeout:
            pass                        # cooperative wait timeout
        except Exception as e:
            # A socket-level timeout class is fine too.
            assert 'time' in type(e).__name__.lower(), e
        else:
            raise AssertionError('expected a timeout')
    finally:
        r.close()
        w.close()


# ---------------------------------------------------------------------------
# TCP: py2-style accept, connect_ex, getpeername, dup, shutdown, setblocking,
# construction from fileno
# ---------------------------------------------------------------------------

def _tcp_listener():
    srv = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM)
    srv.bind(('127.0.0.1', 0))
    srv.listen(5)
    return srv, srv.getsockname()


def test_tcp_accept_py2_style_and_getpeername():
    srv, addr = _tcp_listener()
    cli = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM)
    try:
        # accept() parks first (cooperative accept path), connect wakes it.
        acceptor = filament.spawn(srv.accept)
        filament.sleep(0.02)
        cli.connect(addr)
        conn, peer = acceptor.wait()
        try:
            assert peer[0] == '127.0.0.1'
            assert cli.getpeername()[:2] == addr[:2]
            cli.sendall(b'hello')
            assert conn.recv(16) == b'hello'
        finally:
            conn.close()
    finally:
        cli.close()
        srv.close()


def test_tcp_connect_ex_success_and_refused():
    srv, addr = _tcp_listener()
    cli = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM)
    try:
        assert cli.connect_ex(addr) == 0
    finally:
        cli.close()
        srv.close()

    # Grab a port that is certainly closed.
    probe = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM)
    probe.bind(('127.0.0.1', 0))
    dead_addr = probe.getsockname()
    probe.close()

    cli2 = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM)
    try:
        import errno
        assert cli2.connect_ex(dead_addr) == errno.ECONNREFUSED
    finally:
        cli2.close()


def test_getpeername_unconnected_raises():
    s = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            s.getpeername()
    finally:
        s.close()


def test_tcp_dup_and_shutdown():
    srv, addr = _tcp_listener()
    cli = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM)
    try:
        acceptor = filament.spawn(srv.accept)
        cli.connect(addr)
        conn, _peer = acceptor.wait()
        try:
            d = cli.dup()
            try:
                assert d.fileno() != cli.fileno()
                d.sendall(b'via-dup')
                assert conn.recv(16) == b'via-dup'
            finally:
                d.close()
            # Half-close: the peer reads EOF.
            cli.shutdown(std_socket.SHUT_WR)
            assert conn.recv(16) == b''
        finally:
            conn.close()
    finally:
        cli.close()
        srv.close()


def test_shutdown_unconnected_raises():
    s = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            s.shutdown(std_socket.SHUT_RDWR)
    finally:
        s.close()


def test_setblocking_toggles_timeout():
    s = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM)
    try:
        s.setblocking(False)
        assert s.gettimeout() == 0.0
        s.setblocking(True)
        assert s.gettimeout() is None
    finally:
        s.close()


def test_socket_from_fileno():
    srv, addr = _tcp_listener()
    cli = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM)
    try:
        acceptor = filament.spawn(srv.accept)
        cli.connect(addr)
        conn, _peer = acceptor.wait()
        try:
            d = cli.dup()
            s2 = _csocket.socket(std_socket.AF_INET, std_socket.SOCK_STREAM,
                                 0, d.detach())
            try:
                assert s2.getpeername()[:2] == addr[:2]
                s2.sendall(b'via-fileno')
                assert conn.recv(16) == b'via-fileno'
            finally:
                s2.close()
        finally:
            conn.close()
    finally:
        cli.close()
        srv.close()


# ---------------------------------------------------------------------------
# resolver control functions (global state -> fresh subprocess)
# ---------------------------------------------------------------------------

def test_resolver_module_controls_subprocess():
    res = run_py('''
import filament
import _filament.socket as cs

methods = cs.fil_resolver_method_list()
assert b'getaddrinfo' in methods, methods
assert b'gethostbyname' in methods, methods

# Install a resolver INSTANCE explicitly (fil_set_resolver stores the object
# and calls the methods on it, so a class would end up as an unbound call
# with the hostname as self); then a lookup works through it.
from filament.thrpool_resolver import get_resolver
cs.fil_set_resolver(get_resolver())
assert cs.gethostbyname('localhost')

# An object missing the resolver methods is rejected.
class Bogus(object):
    pass
try:
    cs.fil_set_resolver(Bogus())
except TypeError:
    pass
else:
    raise AssertionError('expected TypeError for bogus resolver')
print("OK")
''')
    assert res.ok(), repr(res)
    assert 'OK' in res.stdout


# ---------------------------------------------------------------------------
# _filament.queue: Queue and SimpleQueue direct API
# ---------------------------------------------------------------------------

def test_cqueue_introspection_and_nowait():
    q = _cqueue.Queue(1)
    assert q.qsize() == 0
    assert q.empty() is True
    assert q.full() is False
    q.put_nowait('a')
    assert q.qsize() == 1
    assert q.empty() is False
    assert q.full() is True
    with pytest.raises(_cqueue.Full):
        q.put_nowait('b')
    assert q.get_nowait() == 'a'
    with pytest.raises(_cqueue.Empty):
        q.get_nowait()


def test_csimple_queue_api():
    sq = _cqueue.SimpleQueue()
    assert sq.empty() is True
    assert sq.qsize() == 0
    assert len(sq) == 0
    sq.put_nowait(1)
    sq.put(2)
    assert sq.qsize() == 2
    assert len(sq) == 2
    assert sq.get_nowait() == 1
    assert sq.get() == 2
    with pytest.raises(_cqueue.Empty):
        sq.get_nowait()
    # Parked get woken by a later put.
    getter = filament.spawn(sq.get)
    filament.sleep(0.02)
    sq.put('late')
    assert getter.wait() == 'late'


# ---------------------------------------------------------------------------
# _filament.core: Filament construction, Scheduler methods, TSD destructor
# ---------------------------------------------------------------------------

def test_filament_direct_construction():
    f = _core.Filament(lambda x: x + 1, 41)
    assert f.wait() == 42
    with pytest.raises(TypeError):
        _core.Filament()               # needs at least the callable


def test_scheduler_methods_subprocess():
    # Scheduler methods poke at per-thread runtime state; keep the experiment
    # in a throwaway process (run_py's watchdog turns a hang into a failure).
    res = run_py('''
import filament
import _filament.core as core

filament.spawn(filament.sleep, 0).wait()   # ensure this thread has a scheduler

# Scheduler() hands back the current thread's scheduler; its greenlet is the
# live scheduler greenlet.
sched = core.Scheduler()
gl = sched.greenlet()
assert gl is not None
# switch() enters the scheduler loop and only comes back when something in
# the event queue switches back to us -- so enqueue exactly that first via
# fil_switch (a manual cooperative yield, spelled out).
import _fil_greenlet
me = _fil_greenlet.getcurrent()
sched.fil_switch(me)
sched.switch()
print("OK")
''')
    assert res.ok(), repr(res)
    assert 'OK' in res.stdout


def test_scheduler_tsd_destructor_on_thread_exit():
    # A real OS thread that creates a scheduler (first cooperative call does)
    # must tear it down via the pthread TSD destructor when the thread exits.
    done = []

    def body():
        filament.spawn(filament.sleep, 0).wait()
        done.append(True)

    t = std_threading.Thread(target=body)
    t.start()
    t.join(30)
    assert done == [True]


# ---------------------------------------------------------------------------
# io thread: multi-waiter same (fd, direction) -> classic wait path
# ---------------------------------------------------------------------------

def test_two_waiters_same_fd():
    # The persistent edge-triggered fast path only serves a single parked
    # waiter per (fd, direction); a second concurrent waiter must take the
    # classic multi-waiter path.  Park two readers on ONE datagram socket,
    # then send two datagrams; both must complete.
    r, w, addr = _udp_pair()
    try:
        readers = [filament.spawn(r.recvfrom, 16) for _ in range(2)]
        filament.sleep(0.05)           # both parked
        w.sendto(b'one', addr)
        w.sendto(b'two', addr)
        got = sorted(reader.wait()[0] for reader in readers)
        assert got == [b'one', b'two']
    finally:
        r.close()
        w.close()


# ---------------------------------------------------------------------------
# fil_io: negative-fd EBADF guards, abstimeout helper, FDesc
# ---------------------------------------------------------------------------

def test_io_negative_fd_raises_ebadf():
    import errno
    import _filament.io as _io
    for attempt in (lambda: _io.os_read(-1, 10),
                    lambda: _io.os_write(-1, b'x'),
                    lambda: _io.fd_wait_read_ready(-1),
                    lambda: _io.fd_wait_write_ready(-1)):
        with pytest.raises(OSError) as ei:
            attempt()
        assert ei.value.errno == errno.EBADF


def test_io_abstimeout_and_fdesc():
    import _filament.io as _io
    assert _io.abstimeout_from_timeout(None) is None
    abst = _io.abstimeout_from_timeout(0.5)
    assert isinstance(abst, tuple)
    fd = _io.FDesc(3)
    assert int(fd) == 3
    assert fd + 0 == 3                  # behaves as its integer value


def test_fd_wait_timeout_and_custom_exc():
    import os as std_os
    import _filament.io as _io
    rfd, wfd = std_os.pipe()
    try:
        with pytest.raises(fil_exc.Timeout):
            _io.fd_wait_read_ready(rfd, timeout=0.05)

        class Custom(Exception):
            pass

        def _raise_custom():
            raise Custom('fd wait timed out')

        with pytest.raises(Custom):
            _io.fd_wait_read_ready(rfd, timeout=0.05,
                                   timeout_exc=_raise_custom)
    finally:
        std_os.close(rfd)
        std_os.close(wfd)


# ---------------------------------------------------------------------------
# thrpool: direct ThreadPool API
# ---------------------------------------------------------------------------

def test_threadpool_direct_api():
    import _filament.thrpool as _tp
    p = _tp.ThreadPool(min_threads=1, max_threads=2, stack_size=131072)
    try:
        assert p.is_shutdown is False
        assert p.run(lambda a, b: a + b, 1, 2) == 3

        # run()'s kwargs= hands the dict to fn as a literal 'kwargs' keyword.
        def kwfn(x, kwargs=None):
            return (x, kwargs)
        assert p.run(kwfn, 5, kwargs={'k': 1}) == (5, {'k': 1})

        # timeout=0: fire-and-forget, result discarded.
        assert p.run(lambda: 99, timeout=0) is None
    finally:
        p.shutdown()
    assert p.is_shutdown is True
    with pytest.raises(RuntimeError):
        p.run(lambda: 1)
    with pytest.raises(RuntimeError):
        p.shutdown()                    # second shutdown is an error


def test_threadpool_min_above_max_clamped():
    import _filament.thrpool as _tp
    p = _tp.ThreadPool(min_threads=5, max_threads=2)
    try:
        assert p.run(lambda: 'ok') == 'ok'
    finally:
        p.shutdown()


# ---------------------------------------------------------------------------
# locking primitives: error paths and introspection
# ---------------------------------------------------------------------------

def test_lock_release_without_acquire():
    import _filament.locking as _lk
    with pytest.raises(RuntimeError):
        _lk.Lock().release()
    with pytest.raises(RuntimeError):
        _lk.RLock().release()


def test_rlock_recursion_and_locked():
    import _filament.locking as _lk
    r = _lk.RLock()
    assert r.locked() is False
    r.acquire()
    r.acquire()
    assert r.locked() is True
    r.release()
    assert r.locked() is True           # still held once
    r.release()
    assert r.locked() is False


def test_condition_without_lock_held():
    import _filament.locking as _lk
    c = _lk.Condition(_lk.Lock())
    # NB: the C primitive lets notify() through without the lock held (the
    # pure-python threading wrapper enforces the stdlib contract); wait()
    # fails when it tries to release the un-held lock.
    c.notify()
    c.notify_all()
    c.notifyAll()
    with pytest.raises(RuntimeError):
        c.wait(timeout=0.05)


def test_semaphore_counter_and_nonblocking():
    import _filament.locking as _lk
    s = _lk.Semaphore(2)
    assert s.counter == 2
    assert s.locked() is False
    s.acquire()
    s.acquire()
    assert s.counter == 0
    assert s.locked() is True
    assert s.acquire(blocking=False) is False
    s.release()
    assert s.acquire(blocking=False) is True
    s.release()
    s.release()


# ---------------------------------------------------------------------------
# core: spawn validation, Filament kwargs, Message error paths
# ---------------------------------------------------------------------------

def test_spawn_non_callable_rejected():
    with pytest.raises(TypeError):
        filament.spawn(42)


def test_filament_kwargs_and_join():
    def fn(a, b=None):
        return (a, b)

    f = _core.Filament(fn, 1, b=2)
    assert f.wait() == (1, 2)

    f2 = filament.spawn(fn, 3, b=4)
    f2.join()                           # greenthread-style alias
    assert f2.dead


def test_message_double_send_and_exception():
    import sys
    m = _core.Message()
    m.send('v')
    with pytest.raises(RuntimeError):
        m.send('again')
    # wait() is repeatable once resolved.
    assert m.wait() == 'v'
    assert m.wait() == 'v'

    m2 = _core.Message()
    try:
        raise ValueError('boom')
    except ValueError:
        m2.send_exception(*sys.exc_info())
    with pytest.raises(ValueError):
        m2.wait()
