import testtools

import filament
from filament import socket
from _filament import socket as _socket
from _filament import queue

class SocketTestCase(testtools.TestCase):
    def test_basic(self):
        self.assertIn('filament', socket.__file__)
        self.assertEqual(1, socket.SOCK_STREAM)
        self.assertEqual(2, socket.AF_INET)
        s = socket.socket()
        self.assertEqual(_socket.socket, type(s._sock))
        self.assertEqual(socket.AF_INET, s.family)
        self.assertEqual(socket.SOCK_STREAM, s.type)
        self.assertEqual(_socket.socket, type(s._sock))
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

        # check the module's _realsocket is correct
        self.assertEqual(_fsocket.socket, fsocket._realsocket)
        self.assertEqual(_psocket.socket, psocket1._realsocket)
        self.assertEqual(_psocket.socket, psocket2._realsocket)
        self.assertNotEqual(_fsocket.socket, _psocket.socket)
        self.assertNotEqual(psocket1.gethostbyname, fsocket.gethostbyname)
        self.assertEqual(psocket1.gethostbyname, psocket2.gethostbyname)
        self.assertEqual(_fsocket.socket, type(fsocket.socket()._sock))
        self.assertEqual(_psocket.socket, type(psocket1.socket()._sock))
        self.assertEqual(_psocket.socket, type(psocket2.socket()._sock))

    def test_dup(self):
        sock = socket.socket()
        nsock = sock.dup()
        self.assertNotEqual(sock, nsock)
        self.assertEqual(sock._sock, nsock._sock)
        self.assertEqual(_socket.socket, type(nsock._sock))

    def test_connect_accept(self):
        q = queue.Queue()

        def _listener():
            listener = socket.socket()
            listener.bind(('0.0.0.0', 0))
            listener.listen(128)
            q.put(listener.getsockname())
            ns, peer = listener.accept()
            q.put(peer)
            data = ''
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
        s.send('hi there')
        s.close()
        self.assertEqual('hi there', q.get())
        thr.wait()

        # test create_connection() also
        thr = filament.spawn(_listener)
        s = socket.create_connection(q.get())
        self.assertEqual(_socket.socket, type(s._sock))
        self.assertEqual(s.getsockname(), q.get())
        s.send('hi there again')
        s.close()
        self.assertEqual('hi there again', q.get())
        thr.wait()
