import sys

import testtools

import filament
from filament import socket
from _filament import socket as _socket
from _filament import queue

_PY3 = sys.version_info[0] >= 3


def _lowlevel(sock):
    # Return the low-level socket that isinstance() checks should target.
    #
    # On Python 3 the stdlib high-level socket *subclasses* the low-level
    # socket (filament's cooperative _filament.socket.socket, or the real
    # _socket.socket), so the high-level object is itself an instance of the
    # low-level type.  On Python 2 the high-level 'socket._socketobject'
    # instead *wraps* the low-level socket as 'self._sock' -- a documented,
    # intentional architectural divergence (see filament/socket.py).  So on
    # Python 2 the isinstance relationship holds on '._sock', not on the
    # wrapper itself.  This keeps the coverage identical on both Pythons while
    # respecting each one's socket object model.
    return sock if _PY3 else sock._sock


class SocketTestCase(testtools.TestCase):
    def test_basic(self):
        self.assertIn('filament', socket.__file__)
        self.assertEqual(1, socket.SOCK_STREAM)
        self.assertEqual(2, socket.AF_INET)
        s = socket.socket()
        # On py3 the stdlib high-level socket subclasses filament's cooperative
        # _filament.socket.socket, so instances are filament sockets. On py2 the
        # relationship is via s._sock (see _lowlevel()).
        self.assertIsInstance(_lowlevel(s), _socket.socket)
        self.assertEqual(socket.AF_INET, s.family)
        self.assertEqual(socket.SOCK_STREAM, s.type)
        self.assertIsInstance(_lowlevel(s), _socket.socket)
        self.assertEqual(0, s.proto)
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, 0)
        self.assertEqual(socket.AF_INET6, s.family)
        self.assertEqual(socket.SOCK_DGRAM, s.type)
        self.assertEqual(0, s.proto)

    def test_filament_socket_doesnt_touch_orig_socket(self):
        # Tests that importing filament.socket doesn't
        # monkey patch 'socket', '_socket', etc. Duplicates
        # a lil bit of test_basic().
        from _filament import socket as _fsocket
        import socket as psocket1
        from filament import socket as fsocket
        import socket as psocket2
        import _socket as _psocket

        self.assertIn('filament', fsocket.__file__)
        self.assertNotIn('filament', psocket1.__file__)

        # filament.socket exposes _realsocket pointing at the cooperative
        # _filament.socket.socket (mirrors the py2 stdlib _realsocket alias).
        self.assertEqual(_fsocket.socket, fsocket._realsocket)
        # The real socket/_socket modules are left untouched, and the filament
        # cooperative socket type is distinct from the real _socket.socket.
        self.assertNotEqual(_fsocket.socket, _psocket.socket)
        self.assertNotEqual(psocket1.gethostbyname, fsocket.gethostbyname)
        self.assertEqual(psocket1.gethostbyname, psocket2.gethostbyname)
        # filament sockets are cooperative _filament.socket.socket instances;
        # the real socket module still yields real _socket.socket instances.
        self.assertIsInstance(_lowlevel(fsocket.socket()), _fsocket.socket)
        self.assertIsInstance(_lowlevel(psocket1.socket()), _psocket.socket)
        self.assertIsInstance(_lowlevel(psocket2.socket()), _psocket.socket)

    def test_dup(self):
        sock = socket.socket()
        nsock = sock.dup()
        self.assertNotEqual(sock, nsock)
        # dup() returns a new, distinct cooperative socket with its own fd.
        self.assertNotEqual(sock.fileno(), nsock.fileno())
        self.assertIsInstance(_lowlevel(nsock), _socket.socket)

    def test_connect_accept(self):
        q = queue.Queue()

        def _listener():
            listener = socket.socket()
            listener.bind(('0.0.0.0', 0))
            listener.listen(128)
            q.put(listener.getsockname())
            ns, peer = listener.accept()
            q.put(peer)
            data = b''
            while 1:
                # just to exercise things, read 1 byte at a time
                d = ns.recv(1)
                if not d:
                    break
                data += d
            q.put(data)

        thr = filament.spawn(_listener)
        s = socket.socket()
        s.connect(q.get())
        self.assertEqual(s.getsockname(), q.get())
        s.send(b'hi there')
        s.close()
        self.assertEqual(b'hi there', q.get())
        thr.wait()

        # test create_connection() also
        thr = filament.spawn(_listener)
        s = socket.create_connection(q.get())
        # On py3 the high-level socket subclasses filament's cooperative socket
        # (_filament.socket.socket); on py2 it wraps it as s._sock (_lowlevel()).
        self.assertIsInstance(_lowlevel(s), _socket.socket)
        self.assertEqual(s.getsockname(), q.get())
        s.send(b'hi there again')
        s.close()
        self.assertEqual(b'hi there again', q.get())
        thr.wait()
