# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Additional cooperative SSL coverage tests (py3 branch of filament/ssl.py):

  * setblocking()/getblocking() overrides + the non-blocking (timeout==0.0)
    re-raise guards in the cooperative wait helpers
  * the read/write "_timed_out" closures (cooperative timeouts)
  * SSLSocket.write() and large sendall() retry loops (SSLWantWriteError)
  * cooperative unwrap() returning the plain socket
  * the module-level wrap_socket() compat shim kwargs (ca_certs / ciphers /
    cert_reqs)

Skips gracefully if no certificate generator (openssl) is available.
"""

from __future__ import absolute_import

import fcntl
import os

import pytest

import filament
from filament import socket as fsocket
from _filament import queue as cqueue

from tests._helpers import make_self_signed_cert

_CERT, _KEY = make_self_signed_cert()
_no_cert = pytest.mark.skipif(_CERT is None,
                              reason="no self-signed cert generator available")

# Small (kernel will round up) socket buffers so want-write triggers quickly.
_SMALL_BUF = 8192


def run(fn):
    return filament.spawn(fn).wait()


def _listener(q):
    """Bind a listener on loopback and publish its address on q."""
    ls = fsocket.socket()
    ls.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_REUSEADDR, 1)
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)
    q.put(ls.getsockname())
    return ls


def _fd_is_nonblocking(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    return bool(flags & os.O_NONBLOCK)


@_no_cert
def test_ssl_setblocking_getblocking_and_nonblocking_read():
    # Covers the setblocking/getblocking overrides and the timeout==0.0
    # re-raise guard on the read side (SSLWantReadError must propagate).
    from filament import ssl as fssl

    def body():
        q = cqueue.Queue()

        def server():
            ls = _listener(q)
            conn, _addr = ls.accept()
            sconn = fssl.wrap_socket(conn, server_side=True,
                                     certfile=_CERT, keyfile=_KEY)
            # Send nothing; just hold the connection open briefly.
            filament.sleep(0.4)
            sconn.close()
            ls.close()

        filament.spawn_n(server)
        addr = q.get()
        c = fsocket.socket()
        c.connect(addr)
        sc = fssl.wrap_socket(c)

        # Default logical timeout is None => "blocking".
        assert sc.getblocking() is True
        assert sc.gettimeout() is None

        sc.setblocking(False)
        assert sc.getblocking() is False
        assert sc.gettimeout() == 0.0
        # Non-blocking read with no data available must raise immediately.
        with pytest.raises(fssl.SSLWantReadError):
            sc.recv(16)

        sc.setblocking(True)
        assert sc.getblocking() is True
        assert sc.gettimeout() is None
        # The real fd stays non-blocking regardless of the logical mode.
        assert _fd_is_nonblocking(sc.fileno())

        sc.close()
        return True

    assert run(body) is True


@_no_cert
def test_ssl_nonblocking_send_want_write():
    # Covers the timeout==0.0 re-raise guard on the write side: fill the
    # (small) socket buffers with the peer not reading until SSLWantWriteError
    # escapes a non-blocking send().
    from filament import ssl as fssl

    def body():
        q = cqueue.Queue()

        def server():
            ls = _listener(q)
            conn, _addr = ls.accept()
            conn.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_RCVBUF, _SMALL_BUF)
            sconn = fssl.wrap_socket(conn, server_side=True,
                                     certfile=_CERT, keyfile=_KEY)
            # Never read; hold the connection open while the client fills it.
            filament.sleep(0.6)
            sconn.close()
            ls.close()

        filament.spawn_n(server)
        addr = q.get()
        c = fsocket.socket()
        c.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_SNDBUF, _SMALL_BUF)
        c.connect(addr)
        sc = fssl.wrap_socket(c)

        sc.setblocking(False)
        chunk = b"x" * 65536
        with pytest.raises(fssl.SSLWantWriteError):
            # Bounded: buffers total well under this, so it must raise.
            for _ in range(256):
                sc.send(chunk)
        sc.close()
        return True

    assert run(body) is True


@_no_cert
def test_ssl_read_timeout():
    # Covers the read-side "_timed_out" closure: a positive logical timeout
    # with a silent peer must raise SSLError("... operation timed out").
    from filament import ssl as fssl

    def body():
        q = cqueue.Queue()

        def server():
            ls = _listener(q)
            conn, _addr = ls.accept()
            sconn = fssl.wrap_socket(conn, server_side=True,
                                     certfile=_CERT, keyfile=_KEY)
            # Handshake done; send nothing so the client read times out.
            filament.sleep(0.6)
            sconn.close()
            ls.close()

        filament.spawn_n(server)
        addr = q.get()
        c = fsocket.socket()
        c.connect(addr)
        sc = fssl.wrap_socket(c)

        sc.settimeout(0.2)
        # Logical timeout is reported while the fd itself stays non-blocking.
        assert sc.gettimeout() == 0.2
        assert _fd_is_nonblocking(sc.fileno())

        with pytest.raises(fssl.SSLError) as excinfo:
            sc.recv(16)
        assert "operation timed out" in str(excinfo.value)
        sc.close()
        return True

    assert run(body) is True


@_no_cert
def test_ssl_write_timeout():
    # Covers the write-side "_timed_out" closure: small buffers, a peer that
    # never reads, and a large sendall() with a short logical timeout.
    from filament import ssl as fssl

    def body():
        q = cqueue.Queue()

        def server():
            ls = _listener(q)
            conn, _addr = ls.accept()
            conn.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_RCVBUF, _SMALL_BUF)
            sconn = fssl.wrap_socket(conn, server_side=True,
                                     certfile=_CERT, keyfile=_KEY)
            # Never read application data.
            filament.sleep(0.8)
            sconn.close()
            ls.close()

        filament.spawn_n(server)
        addr = q.get()
        c = fsocket.socket()
        c.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_SNDBUF, _SMALL_BUF)
        c.connect(addr)
        sc = fssl.wrap_socket(c)

        sc.settimeout(0.2)
        with pytest.raises(fssl.SSLError) as excinfo:
            sc.sendall(b"y" * (4 * 1024 * 1024))
        assert "operation timed out" in str(excinfo.value)
        sc.close()
        return True

    assert run(body) is True


@_no_cert
def test_ssl_write_method_large_slow_reader():
    # Covers the _fil_write retry loop via SSLSocket.write() (distinct from
    # send()): the peer delays reading, so writes hit SSLWantWriteError and
    # must cooperatively wait until the reader drains the buffers.
    from filament import ssl as fssl
    total = 512 * 1024

    def body():
        q = cqueue.Queue()

        def server():
            ls = _listener(q)
            conn, _addr = ls.accept()
            conn.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_RCVBUF, _SMALL_BUF)
            sconn = fssl.wrap_socket(conn, server_side=True,
                                     certfile=_CERT, keyfile=_KEY)
            # Delay so the client's writes fill the buffers and block first.
            filament.sleep(0.3)
            got = 0
            while got < total:
                data = sconn.recv(65536)
                if not data:
                    break
                got += len(data)
            sconn.sendall(b"ok" if got == total else b"??")
            sconn.close()
            ls.close()

        srv = filament.spawn(server)
        addr = q.get()
        c = fsocket.socket()
        c.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_SNDBUF, _SMALL_BUF)
        c.connect(addr)
        sc = fssl.wrap_socket(c)

        chunk = b"z" * 65536
        written = 0
        while written < total:
            n = sc.write(chunk[:min(len(chunk), total - written)])
            assert n > 0
            written += n
        reply = sc.recv(16)
        sc.close()
        srv.wait()
        return reply

    assert run(body) == b"ok"


@_no_cert
def test_ssl_sendall_large_slow_reader():
    # Covers the _fil_send want-write retry path: one large sendall() against
    # a reader that only starts draining after a delay.
    from filament import ssl as fssl
    total = 1024 * 1024

    def body():
        q = cqueue.Queue()

        def server():
            ls = _listener(q)
            conn, _addr = ls.accept()
            conn.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_RCVBUF, _SMALL_BUF)
            sconn = fssl.wrap_socket(conn, server_side=True,
                                     certfile=_CERT, keyfile=_KEY)
            filament.sleep(0.2)
            got = 0
            while got < total:
                data = sconn.recv(65536)
                if not data:
                    break
                got += len(data)
            sconn.sendall(b"ok" if got == total else b"??")
            sconn.close()
            ls.close()

        srv = filament.spawn(server)
        addr = q.get()
        c = fsocket.socket()
        c.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_SNDBUF, _SMALL_BUF)
        c.connect(addr)
        sc = fssl.wrap_socket(c)

        sc.sendall(b"w" * total)
        reply = sc.recv(16)
        sc.close()
        srv.wait()
        return reply

    assert run(body) == b"ok"


@_no_cert
def test_ssl_unwrap_returns_plain_socket():
    # Covers _fil_unwrap: after an echo exchange both sides unwrap(),
    # exchanging close_notify, and each gets the plain socket back.
    from filament import ssl as fssl

    def body():
        q = cqueue.Queue()

        def server():
            ls = _listener(q)
            conn, _addr = ls.accept()
            sconn = fssl.wrap_socket(conn, server_side=True,
                                     certfile=_CERT, keyfile=_KEY)
            data = sconn.recv(100)
            sconn.sendall(data)
            plain = sconn.unwrap()
            # Modern stdlib unwrap() returns the owner socket itself with the
            # TLS layer removed (_sslobj cleared).
            ok = plain is not None and plain._sslobj is None
            plain.close()
            ls.close()
            return ok

        srv = filament.spawn(server)
        addr = q.get()
        c = fsocket.socket()
        c.connect(addr)
        sc = fssl.wrap_socket(c)
        sc.sendall(b"unwrap-me")
        got = sc.recv(100)
        fd = sc.fileno()
        plain = sc.unwrap()
        # unwrap() must return the plain socket back (the fix over the old
        # filament ssl.py which returned nothing): same fd, TLS layer gone.
        assert plain is not None
        assert plain.fileno() == fd
        assert plain._sslobj is None
        plain.close()
        server_ok = srv.wait()
        return got == b"unwrap-me" and server_ok

    assert run(body) is True


@_no_cert
def test_ssl_unwrap_want_write():
    # Covers the _fil_unwrap want-write retry path.  We cannot leave a
    # half-written SSL record pending (calling SSL_shutdown then is invalid
    # OpenSSL usage), so instead the client stuffs *raw* bytes into the kernel
    # send buffer -- bypassing TLS -- until it is full.  unwrap()'s
    # close_notify then hits SSLWantWriteError and must cooperatively wait.
    # The server drains exactly those raw bytes (also bypassing TLS) so the
    # TLS record stream stays aligned for the close_notify exchange.
    from filament import ssl as fssl

    def body():
        q = cqueue.Queue()
        q2 = cqueue.Queue()

        def server():
            ls = _listener(q)
            conn, _addr = ls.accept()
            conn.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_RCVBUF, _SMALL_BUF)
            sconn = fssl.wrap_socket(conn, server_side=True,
                                     certfile=_CERT, keyfile=_KEY)
            # Wait until the client has filled its buffers with raw junk.
            junk = q2.get()
            fd = sconn.fileno()
            got = 0
            while got < junk:
                try:
                    got += len(os.read(fd, min(65536, junk - got)))
                except BlockingIOError:
                    filament.sleep(0.01)
            plain = sconn.unwrap()
            plain.close()
            ls.close()

        srv = filament.spawn(server)
        addr = q.get()
        c = fsocket.socket()
        c.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_SNDBUF, _SMALL_BUF)
        c.connect(addr)
        sc = fssl.wrap_socket(c)

        # Fill the kernel send buffer with raw (non-TLS) bytes; the fd is
        # always non-blocking underneath, so this cannot block.
        fd = sc.fileno()
        junk = 0
        while True:
            try:
                junk += os.write(fd, b"\x00" * 4096)
            except BlockingIOError:
                break
        assert junk > 0
        q2.put(junk)

        plain = sc.unwrap()
        assert plain is not None and plain._sslobj is None
        plain.close()
        srv.wait()
        return True

    assert run(body) is True


@_no_cert
def test_ssl_wrap_socket_compat_kwargs():
    # Covers the wrap_socket() compat shim kwargs: ca_certs, ciphers and
    # cert_reqs. PROTOCOL_TLS keeps check_hostname off so a CN=localhost
    # self-signed cert verifies against itself as the trust root.
    from filament import ssl as fssl

    def body():
        q = cqueue.Queue()

        def server():
            ls = _listener(q)
            conn, _addr = ls.accept()
            sconn = fssl.wrap_socket(conn, server_side=True,
                                     certfile=_CERT, keyfile=_KEY)
            data = sconn.recv(100)
            sconn.sendall(data)
            sconn.close()
            ls.close()

        filament.spawn_n(server)
        addr = q.get()
        c = fsocket.socket()
        c.connect(addr)
        sc = fssl.wrap_socket(c,
                              ssl_version=fssl.PROTOCOL_TLS,
                              ca_certs=_CERT,
                              cert_reqs=fssl.CERT_REQUIRED,
                              ciphers="DEFAULT")
        # Verification actually ran: the peer cert must be available.
        cert = sc.getpeercert()
        assert cert and cert.get("subject")
        sc.sendall(b"verified-payload")
        got = sc.recv(100)
        sc.close()
        return got

    assert run(body) == b"verified-payload"
