# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Cooperative SSL tests: a self-signed cert is generated in the test, then a green
TLS server and client perform a cooperative handshake + echo over it.

Skips gracefully if no certificate generator (openssl) is available.
"""

from __future__ import absolute_import

import pytest

import filament
from filament import socket as fsocket
from _filament import queue as cqueue

from tests._helpers import make_self_signed_cert

_CERT, _KEY = make_self_signed_cert()
_no_cert = pytest.mark.skipif(_CERT is None,
                              reason="no self-signed cert generator available")


def run(fn):
    return filament.spawn(fn).wait()


@_no_cert
def test_ssl_handshake_and_echo():
    from filament import ssl as fssl

    def body():
        q = cqueue.Queue()

        def server():
            ls = fsocket.socket()
            ls.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_REUSEADDR, 1)
            ls.bind(("127.0.0.1", 0))
            ls.listen(1)
            q.put(ls.getsockname())
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
        sc = fssl.wrap_socket(c)          # bare -> no verification
        sc.sendall(b"secret-payload")
        got = sc.recv(100)
        sc.close()
        return got

    assert run(body) == b"secret-payload"


@_no_cert
def test_ssl_concurrent_clients():
    from filament import ssl as fssl

    def body():
        q = cqueue.Queue()
        nconns = 10

        def server():
            ls = fsocket.socket()
            ls.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_REUSEADDR, 1)
            ls.bind(("127.0.0.1", 0))
            ls.listen(nconns)
            q.put(ls.getsockname())

            def handle(conn):
                sconn = fssl.wrap_socket(conn, server_side=True,
                                         certfile=_CERT, keyfile=_KEY)
                data = sconn.recv(100)
                sconn.sendall(data)
                sconn.close()

            hs = []
            for _ in range(nconns):
                conn, _addr = ls.accept()
                hs.append(filament.spawn(handle, conn))
            filament.joinall(hs)
            ls.close()

        filament.spawn_n(server)
        addr = q.get()

        def client(i):
            c = fsocket.socket()
            c.connect(addr)
            sc = fssl.wrap_socket(c)
            payload = ("tls-%d" % i).encode("ascii")
            sc.sendall(payload)
            got = sc.recv(100)
            sc.close()
            return got == payload

        clients = [filament.spawn(client, i) for i in range(nconns)]
        return [g.wait() for g in clients]

    results = run(body)
    assert len(results) == 10 and all(results)
