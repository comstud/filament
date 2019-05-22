from __future__ import absolute_import
import socket as _orig_socket
from socket import *

from filament import io as _fil_io

_real_socket = _orig_socket.socket

_always_proxy_to_orig = (
        'bind', 'close', 'getpeername', 'getsockname', 'getsockopt',
        'ioctl', 'listen', 'shutdown')

_proxy_methods = (
        'connect', 'connect_ex', 'recv', 'recv_info', 'recvfrom',
        'recvfrom_info', 'send', 'sendall', 'sendto', 'sendall')

class _socket_proxy(object):
    def __init__(self, fil_sock):
        self._sock = sock


class _socket_fileno(int):
    slots = ('__weakref__', '_fil_sock')

    def __new__(cls, *args, **kwargs):
        fil_sock = kwargs['_fil_sock']
        del kwargs['_fil_sock']
        obj = super(_socket_fileno, cls).__new__(cls, *args, **kwargs)
        obj._fil_sock = fil_sock
        return obj

    def _fil_timeout(self):
        return self._fil_sock.gettimeout()


class socket(object):
    __slots__ = ('__weakref__', '_sock', '_timeout') + _always_proxy_to_orig

    def __init__(self, *args, **kwargs):
        self._sock = kwargs.get('_sock')
        if self._sock is None:
            if '_sock' in kwargs:
                del kwargs['_sock']
            self._sock = _real_socket(*args, **kwargs)
        self._timeout = self._sock.gettimeout()
        if self._timeout is None:
            self._timeout = -1.0
        self._sock.setblocking(0)
        for key in _always_proxy_to_orig:
            if hasattr(self._sock, key):
                setattr(self, key, getattr(self._sock, key))

    @property
    def family(self):
        return self._sock.family

    @property
    def type(self):
        return self._sock.type

    @property
    def proto(self):
        return self._sock.proto

    def accept(self):
        if self._timeout == 0.0:
            sock, addr = self._sock.accept()
        else:
            pass
        return self.__class__(_sock=sock), addr

    accept.__doc__ = _real_socket.accept.__doc__

    def dup(self):
        return self.__class__(_sock=self._sock.dup())

    dup.__doc__ = _real_socket.dup.__doc__

    def fileno(self):
        return _socket_fileno(self._sock.fileno(), _fil_sock=self)

    fileno.__doc__ = _real_socket.fileno.__doc__

    # py2
    def makefile(self, mode='r', bufsize=-1):
        return _orig_socket._fileobject(self, mode, bufsize)

    makefile.__doc__ = _real_socket.makefile.__doc__

    def setblocking(self, val):
        self._timeout = -1.0 if val else 0.0

    setblocking.__doc__ = _real_socket.setblocking.__doc__

    def settimeout(self, timeout):
        if timeout is None:
            self._timeout = -1.0
        else:
            if timeout < 0.0:
                raise ValueError('Timeout value out of range')
            self._timeout = timeout

    settimeout.__doc__ = _real_socket.settimeout.__doc__

    def gettimeout(self):
        if self._timeout == -1.0:
            return None
        return self._timeout

    gettimeout.__doc__ = _real_socket.gettimeout.__doc__


if hasattr(_orig_socket, 'fromfd'):
    def fromfd(*args, **kwargs):
        return self.__class__(_sock=self._sock.fromfd(*args, **kwargs))
