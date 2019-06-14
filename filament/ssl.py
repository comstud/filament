from __future__ import absolute_import
import errno
import socket
import sys
import time

from _socket import socket as _fil_realsocket

from filament import io as _fio
from filament import exc as _fexc
from filament import _util

_fil_ssl = _util.copy_module('ssl')


class SSLSocket(_fil_ssl.SSLSocket):
    slots = ('__weakref__', '_fil_sock')

    def __new__(cls, sock, *args, **kwargs):
        # orig SSLSocket needs a 'sock' such that sock._sock gives the
        # real python _socket.socket obj.
        if type(sock._sock) is _fil_realsocket:
            # Not passed a filament socket.. don't bother wrapping.
            return _fil_ssl.SSLSocket(sock=sock, **kwargs)
        return super(SSLSocket, cls).__new__(cls, sock, *args, **kwargs)

    def __init__(self, sock, *args, **kwargs):
        # save the original filament sock
        self._fil_sock = sock._sock
        # this will be the '_filament.socket'
        super(SSLSocket, self).__init__(sock._sock, *args, **kwargs)
        # put the _filament.Socket in place. (normally this is the _socket.socket)
        self._sock = self._fil_sock

    def gettimeout(self):
        return self._fil_sock.gettimeout()

    def settimeout(self, val):
        return self._fil_sock.settimeout(val)

    def setblocking(self, val):
        return self._fil_sock.setblocking(val)

    def _get_timeout(self):
        return _fio.abstimeout_from_timeout(self.gettimeout())

    def _fil_wait_read(self, to, operation):
        if self._fil_sock.gettimeout() == 0.0:
            # always called within an exception
            raise
        def _fil_read_timeout():
            raise _fil_ssl.SSLError("The %s operation timed out" % (operation,))
        _fio.fd_wait_read_ready(self._sock.fileno(), abstimeout=to, timeout_exc=_fil_read_timeout)

    def _fil_wait_write(self, to, operation):
        if self._fil_sock.gettimeout() == 0.0:
            # always called within an exception
            raise
        def _fil_write_timeout():
            raise _fil_ssl.SSLError("The %s operation timed out" % (operation,))
        _fio.fd_wait_write_ready(self._sock.fileno(), abstimeout=to, timeout_exc=_fil_write_timeout)

    def _real_connect(self, addr, connect_ex):
        # do some tricky stuff, as this needs the _socket.socket as self._sock
        # to create the SSL context, but then it'll also be used for socket.connect()
        # which is always in non-block mode.
        to = self._get_timeout()
        orig_sock = self._sock
        self._sock = orig_sock._sock
        try:
            while 1:
                try:
                    rc = super(SSLSocket, self)._real_connect(addr, connect_ex)
                    if rc == errno.EINPROGRESS:
                        raise _fil_ssl.socket_error((rc, 'in progress'))
                    return rc
                except _fil_ssl.socket_error as exc:
                    if exc.args[0] == errno.EINPROGRESS:
                        self._fil_wait_write(to, 'connect')
                    else:
                        raise
        finally:
            self._sock = orig_sock

    def do_handshake(self, *args, **kwargs):
        to = self._get_timeout()
        while 1:
            try:
                return super(SSLSocket, self).do_handshake(*args, **kwargs)
            except _fil_ssl.SSLWantReadError:
                self._fil_wait_read(to, 'handshake')
            except _fil_ssl.SSLWantWriteError:
                self._fil_wait_write(to,'handshake')

    do_handshake.__doc__ = _fil_ssl.SSLSocket.do_handshake.__doc__

    def read(self, *args, **kwargs):
        to = self._get_timeout()
        while 1:
            try:
                return super(SSLSocket, self).read(*args, **kwargs)
            except _fil_ssl.SSLWantReadError:
                self._fil_wait_read(to, 'read')
            except _fil_ssl.SSLWantWriteError:
                self._fil_wait_write(to, 'read')

    read.__doc__ = _fil_ssl.SSLSocket.read.__doc__

    def write(self, *args, **kwargs):
        to = self._get_timeout()
        while 1:
            try:
                res = super(SSLSocket, self).write(*args, **kwargs)
                if res == 0:
                    pass
            except _fil_ssl.SSLWantReadError:
                self._fil_wait_read(to, 'write')
            except _fil_ssl.SSLWantWriteError:
                self._fil_wait_write(to, 'write')

    write.__doc__ = _fil_ssl.SSLSocket.write.__doc__

    def unwrap(self):
        to = self._get_timeout()
        while 1:
            try:
                res = super(SSLSocket, self).unwrap()
            except _fil_ssl.SSLWantReadError:
                self._fil_wait_read(to, 'shutdown')
            except _fil_ssl.SSLWantWriteError:
                self._fil_wait_write(to, 'shutdown')

    unwrap.__doc__ = _fil_ssl.SSLSocket.unwrap.__doc__




# Replacing this in the 'ssl' dict
# allows us to not have to subclass SSLContext
# to wrap it's wrap_socket() method with one that
# creates our sSLSocket. Instead, this makes the
# wrap_context() find ours:
_fil_ssl.SSLSocket = SSLSocket

def sslwrap_simple(sock, keyfile=None, certfile=None):
    return SSLSocket(sock, keyfile=keyfile, certfile=certfile)

_util.copy_globals(_fil_ssl, globals())
