# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Cooperative socket / select IO tests: TCP echo, dup, settimeout, socketpair,
recv_into, create_connection, 100 concurrent clients against one echo server,
and cooperative select readiness.
"""

from __future__ import absolute_import

import pytest

import filament
from filament import socket as fsocket
from _filament import queue as cqueue


def run(fn):
    return filament.spawn(fn).wait()


def _echo_server(nconns=1, chunk=65536):
    """Spawn an echo server greenthread; return its (host, port)."""
    q = cqueue.Queue()

    def server():
        ls = fsocket.socket()
        ls.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_REUSEADDR, 1)
        ls.bind(("127.0.0.1", 0))
        ls.listen(128)
        q.put(ls.getsockname())

        def handle(conn):
            try:
                while True:
                    data = conn.recv(chunk)
                    if not data:
                        break
                    conn.sendall(data)
            finally:
                conn.close()

        handlers = []
        for _ in range(nconns):
            conn, _addr = ls.accept()
            handlers.append(filament.spawn(handle, conn))
        filament.joinall(handlers)
        ls.close()

    filament.spawn_n(server)
    return q.get()


def test_tcp_echo_bytes():
    def body():
        addr = _echo_server()
        s = fsocket.socket()
        s.connect(addr)
        s.sendall(b"hello world")
        got = b""
        while len(got) < len(b"hello world"):
            got += s.recv(100)
        s.close()
        return got

    assert run(body) == b"hello world"


def test_create_connection():
    def body():
        addr = _echo_server()
        s = fsocket.create_connection(addr)
        s.sendall(b"cc")
        got = s.recv(100)
        s.close()
        return got

    assert run(body) == b"cc"


def test_socket_dup():
    def body():
        addr = _echo_server()
        s = fsocket.create_connection(addr)
        d = s.dup()
        assert d.fileno() != s.fileno()
        d.sendall(b"dup-data")
        got = d.recv(100)
        d.close()
        s.close()
        return got

    assert run(body) == b"dup-data"


def test_settimeout_raises_on_idle_recv():
    def body():
        addr = _echo_server()
        s = fsocket.create_connection(addr)
        s.settimeout(0.05)
        out = []
        try:
            s.recv(100)          # server never sends unsolicited data
            out.append("recv-returned")
        except Exception as e:
            # stdlib raises socket.timeout (an OSError subclass); accept any.
            out.append(type(e).__name__)
        s.close()
        return out

    result = run(body)
    assert result and result[0] != "recv-returned"


def test_socketpair_echo():
    def body():
        a, b = fsocket.socketpair()

        def responder():
            data = b.recv(100)
            b.sendall(data.upper())

        g = filament.spawn(responder)
        a.sendall(b"abc")
        got = a.recv(100)
        g.wait()
        a.close()
        b.close()
        return got

    assert run(body) == b"ABC"


def test_recv_into():
    def body():
        addr = _echo_server()
        s = fsocket.create_connection(addr)
        s.sendall(b"buffer!")
        buf = bytearray(7)
        n = s.recv_into(buf)
        s.close()
        return bytes(buf[:n])

    assert run(body) == b"buffer!"


def test_100_concurrent_clients():
    def body():
        addr = _echo_server(nconns=100)

        def client(i):
            s = fsocket.create_connection(addr)
            payload = ("client-%d" % i).encode("ascii")
            s.sendall(payload)
            got = b""
            while len(got) < len(payload):
                got += s.recv(100)
            s.close()
            return got == payload

        clients = [filament.spawn(client, i) for i in range(100)]
        return [g.wait() for g in clients]

    results = run(body)
    assert len(results) == 100
    assert all(results)


def test_select_readiness():
    from filament import select as fselect

    def body():
        a, b = fsocket.socketpair()

        def writer():
            filament.sleep(0.02)
            a.sendall(b"x")

        filament.spawn_n(writer)
        r, w, x = fselect.select([b], [], [], 1.0)
        got = b.recv(1) if b in r else None
        a.close()
        b.close()
        return (b in r), got

    ready, got = run(body)
    assert ready is True
    assert got == b"x"


def test_select_timeout_empty():
    from filament import select as fselect

    def body():
        a, b = fsocket.socketpair()
        r, w, x = fselect.select([b], [], [], 0.03)  # nothing to read
        a.close()
        b.close()
        return r, w, x

    r, w, x = run(body)
    assert r == [] and w == [] and x == []
