"""Filament SSL/TLS support.

Like filament.socket, this module (ab)uses a private copy of the stdlib 'ssl'
module so that TLS runs cooperatively with filaments instead of blocking the
whole process. Importing it does NOT monkey patch the real 'ssl' module.

How it works (Python 3)
-----------------------
The stdlib 'ssl' module performs its TLS record I/O in a C ``_sslobj`` (an
``_ssl._SSLSocket``) that reads/writes the *raw* file descriptor directly. When
that fd is non-blocking, the crypto operations raise ``SSLWantReadError`` /
``SSLWantWriteError`` instead of blocking -- meaning "I need the fd to become
readable/writable before I can make progress."

We want two things at once that normally conflict:

* The underlying fd must be **non-blocking** so the crypto layer emits the
  SSLWant* signals (which we can turn into cooperative waits) rather than
  blocking the OS thread.
* The socket's *logical* timeout must be reported as non-zero (e.g. None) so the
  stdlib treats it as a blocking-style socket -- otherwise ``SSLSocket._create``
  refuses to run ``do_handshake()`` on it and our retry loops never engage.

The C TLS engine (``_ssl._SSLContext._wrap_socket``) also insists that the
object handed to it be a real, weak-referenceable ``_socket.socket``. A
filament cooperative socket is not a ``_socket.socket``, so we cannot simply
masquerade it here (that is what filament.socket does for plain sockets). So on
Python 3 we instead:

1. Import a private copy of 'ssl' (leaving the real 'ssl' untouched). Its
   ``SSLSocket`` therefore subclasses the *real* ``socket.socket`` -- exactly
   what the C engine requires.

2. Decouple the logical timeout from the fd's blocking state by overriding
   ``settimeout``/``gettimeout``/``setblocking``/``getblocking``: we remember the
   caller's intended (logical) timeout, but always keep the real fd
   non-blocking. So ``gettimeout()`` reports None/seconds while the fd underneath
   stays non-blocking -- giving us both properties above.

3. Wrap ``do_handshake``/``read``/``write``/``send``/``unwrap`` in retry loops:
   when the crypto layer raises SSLWantReadError/SSLWantWriteError we ask the
   filament io thread to wait for the fd to become readable/writable
   (``fd_wait_read_ready`` / ``fd_wait_write_ready``). Those calls yield the
   current greenthread back to the scheduler -- other filaments run while this
   one is parked -- and resume it once the fd is ready, whereupon we retry the
   crypto operation. This turns a "blocking-looking" TLS API into one that
   cooperates with the scheduler without ever blocking the OS thread.

``SSLContext.wrap_socket(green_sock)`` consumes a filament cooperative socket
(using its fileno/detach) and returns one of our cooperative ``SSLSocket``s.
"""

from __future__ import absolute_import

import sys

from filament import io as _fil_io
from filament import _util as _fil_util

# The greening/patch machinery keys off this marker.
__filament__ = {"patch": "ssl"}

_PY3 = sys.version_info[0] >= 3


if _PY3:
    # ------------------------------------------------------------------ Py3 --
    # A private copy of stdlib 'ssl'. No masquerade: our SSLSocket must remain a
    # genuine socket.socket / _socket.socket for the C TLS engine. copy_module
    # imports it fresh and restores sys.modules, so the real 'ssl' is untouched.
    _fil_ssl = _fil_util.copy_module('ssl')

    _SSLSocket = _fil_ssl.SSLSocket
    _SSLWantReadError = _fil_ssl.SSLWantReadError
    _SSLWantWriteError = _fil_ssl.SSLWantWriteError
    _SSLError = _fil_ssl.SSLError

    # We patch methods *in place* on our private SSLSocket class rather than
    # subclassing: ssl.SSLSocket._create (used by SSLContext.wrap_socket)
    # internally does ``super(SSLSocket, self).__init__(...)`` resolving
    # 'SSLSocket' from the module globals, and it explicitly forbids public
    # subclass construction. Mutating the class object keeps _create working
    # while making every instance cooperative. This copy of ssl is private, so
    # the real ssl module is unaffected.
    _orig_do_handshake = _SSLSocket.do_handshake
    _orig_read = _SSLSocket.read
    _orig_write = _SSLSocket.write
    _orig_send = _SSLSocket.send
    _orig_unwrap = _SSLSocket.unwrap
    _orig_settimeout = _SSLSocket.settimeout

    # --- timeout / blocking decoupling -------------------------------------
    #
    # We keep the OS-level fd non-blocking at all times (so the crypto layer
    # raises SSLWant* rather than blocking) while separately tracking the
    # caller's intended "logical" timeout that our cooperative retry loops honor.

    def _fil_settimeout(self, timeout):
        # Remember the logical timeout, but force the real fd non-blocking.
        # (None => cooperative block forever, 0.0 => truly non-blocking,
        #  >0 => cooperative wait with that deadline.)
        self._fil_timeout = timeout
        _orig_settimeout(self, 0.0)

    def _fil_gettimeout(self):
        return getattr(self, '_fil_timeout', None)

    def _fil_setblocking(self, flag):
        # gettimeout/settimeout below are the cooperative overrides installed on
        # the class, so go through the public names.
        self.settimeout(None if flag else 0.0)

    def _fil_getblocking(self):
        return self.gettimeout() != 0.0

    # --- cooperative readiness waits ---------------------------------------

    def _fil_deadline(self):
        # Convert the logical (relative) timeout into the absolute-deadline form
        # that fd_wait_*_ready() expect. None => no deadline (wait forever).
        return _fil_io.abstimeout_from_timeout(self.gettimeout())

    def _fil_wait_read(self, deadline, operation):
        # NOTE: only ever called from inside an ``except SSLWant*`` handler.
        # In non-blocking mode (logical timeout 0) the caller expects the
        # SSLWant* exception to propagate, so re-raise it instead of waiting.
        if self.gettimeout() == 0.0:
            raise

        def _timed_out():
            raise _SSLError("The %s operation timed out" % (operation,))

        # Park this greenthread until the fd is readable (or the deadline
        # fires). The io thread wakes us; the process is never blocked.
        _fil_io.fd_wait_read_ready(self.fileno(), abstimeout=deadline,
                                   timeout_exc=_timed_out)

    def _fil_wait_write(self, deadline, operation):
        if self.gettimeout() == 0.0:
            raise

        def _timed_out():
            raise _SSLError("The %s operation timed out" % (operation,))

        _fil_io.fd_wait_write_ready(self.fileno(), abstimeout=deadline,
                                    timeout_exc=_timed_out)

    # --- retry-loop method overrides ---------------------------------------

    def _fil_do_handshake(self, *args, **kwargs):
        deadline = self._fil_deadline()
        while True:
            try:
                return _orig_do_handshake(self, *args, **kwargs)
            except _SSLWantReadError:
                self._fil_wait_read(deadline, 'handshake')
            except _SSLWantWriteError:
                self._fil_wait_write(deadline, 'handshake')

    def _fil_read(self, *args, **kwargs):
        # Backs recv()/recv_into() as well (stdlib SSLSocket routes those here).
        deadline = self._fil_deadline()
        while True:
            try:
                return _orig_read(self, *args, **kwargs)
            except _SSLWantReadError:
                self._fil_wait_read(deadline, 'read')
            except _SSLWantWriteError:
                # A renegotiation can require writing before a read completes.
                self._fil_wait_write(deadline, 'read')

    def _fil_write(self, *args, **kwargs):
        deadline = self._fil_deadline()
        while True:
            try:
                return _orig_write(self, *args, **kwargs)
            except _SSLWantReadError:
                self._fil_wait_read(deadline, 'write')
            except _SSLWantWriteError:
                self._fil_wait_write(deadline, 'write')

    def _fil_send(self, *args, **kwargs):
        # Backs sendall() too (stdlib SSLSocket.sendall loops over send()).
        # stdlib SSLSocket.send() calls self._sslobj.write() directly rather
        # than self.write(), so it needs its own cooperative retry loop.
        deadline = self._fil_deadline()
        while True:
            try:
                return _orig_send(self, *args, **kwargs)
            except _SSLWantReadError:
                self._fil_wait_read(deadline, 'write')
            except _SSLWantWriteError:
                self._fil_wait_write(deadline, 'write')

    def _fil_unwrap(self, *args, **kwargs):
        # Cooperative TLS shutdown. stdlib unwrap() only clears self._sslobj
        # *after* shutdown() succeeds, so retrying on SSLWant* is safe. (The old
        # filament ssl.py had a bug here: it never returned the result -- we
        # return it, as callers expect the plain socket back.)
        deadline = self._fil_deadline()
        while True:
            try:
                return _orig_unwrap(self, *args, **kwargs)
            except _SSLWantReadError:
                self._fil_wait_read(deadline, 'unwrap')
            except _SSLWantWriteError:
                self._fil_wait_write(deadline, 'unwrap')

    # Install the cooperative helpers and overrides on the private class.
    _SSLSocket.settimeout = _fil_settimeout
    _SSLSocket.gettimeout = _fil_gettimeout
    _SSLSocket.setblocking = _fil_setblocking
    _SSLSocket.getblocking = _fil_getblocking
    _SSLSocket._fil_deadline = _fil_deadline
    _SSLSocket._fil_wait_read = _fil_wait_read
    _SSLSocket._fil_wait_write = _fil_wait_write
    _SSLSocket.do_handshake = _fil_do_handshake
    _SSLSocket.read = _fil_read
    _SSLSocket.write = _fil_write
    _SSLSocket.send = _fil_send
    _SSLSocket.unwrap = _fil_unwrap

    # Copy everything (SSLContext, create_default_context, constants, the
    # patched SSLSocket, etc.) into this module's namespace.
    _fil_util.copy_globals(_fil_ssl, globals())

    if 'wrap_socket' not in globals():
        # Modern stdlib ssl (3.12+) removed the module-level wrap_socket()
        # helper in favor of SSLContext.wrap_socket(). Provide a compatibility
        # shim implemented on top of a private SSLContext so legacy callers (and
        # the filament contract) keep working. It returns a cooperative
        # filament SSLSocket.
        def wrap_socket(sock, keyfile=None, certfile=None, server_side=False,
                        cert_reqs=None, ssl_version=None, ca_certs=None,
                        do_handshake_on_connect=True,
                        suppress_ragged_eofs=True, ciphers=None):
            """Wrap an existing (filament) socket in TLS.

            Compatibility shim mirroring the legacy ssl.wrap_socket() signature,
            implemented on the modern SSLContext API. Returns a cooperative
            filament SSLSocket.
            """
            if ssl_version is None:
                ssl_version = PROTOCOL_TLS_SERVER if server_side \
                    else PROTOCOL_TLS_CLIENT
            ctx = SSLContext(ssl_version)
            if certfile is not None:
                ctx.load_cert_chain(certfile, keyfile)
            if ca_certs is not None:
                ctx.load_verify_locations(ca_certs)
            if ciphers is not None:
                ctx.set_ciphers(ciphers)
            if cert_reqs is not None:
                ctx.verify_mode = cert_reqs
            elif not server_side:
                # A bare positional-arg call historically meant "no verification".
                ctx.check_hostname = False
                ctx.verify_mode = CERT_NONE
            return ctx.wrap_socket(
                sock, server_side=server_side,
                do_handshake_on_connect=do_handshake_on_connect,
                suppress_ragged_eofs=suppress_ragged_eofs)

else:  # pragma: no cover -- py2-only branch, unmeasurable on py3
    # ------------------------------------------------------------------ Py2 --
    # Python 2.7's ssl module predates SSLContext.wrap_socket() and builds
    # SSLSocket directly from a socket via SSLSocket(sock, keyfile, certfile)
    # and a _real_connect() helper. We keep the original legacy wrapping here so
    # filament continues to work on 2.7. (Priority is modern Py3; this branch is
    # best-effort and mirrors the historical implementation.)
    import errno

    from _socket import socket as _fil_realsocket

    _fil_ssl = _fil_util.copy_module('ssl')
    _fil_orig_SSLSocket = _fil_ssl.SSLSocket

    class SSLSocket(_fil_ssl.SSLSocket):
        # NB: on Py2, ssl.SSLSocket derives from socket._socketobject, which
        # already provides a __weakref__ slot, so we must NOT redeclare it here
        # ("__weakref__ slot disallowed" TypeError otherwise).  Only the
        # filament-specific attribute needs a slot.
        __slots__ = ('_fil_sock',)

        def __new__(cls, sock, *args, **kwargs):
            # orig SSLSocket needs a 'sock' such that sock._sock gives the
            # real python _socket.socket obj.
            if type(sock._sock) is _fil_realsocket:
                # Not passed a filament socket.. don't bother wrapping.
                return _fil_orig_SSLSocket(sock=sock, **kwargs)
            return super(SSLSocket, cls).__new__(cls, sock, *args, **kwargs)

        def __init__(self, sock, *args, **kwargs):
            # save the original filament sock
            self._fil_sock = sock._sock
            # this will be the '_filament.socket'
            super(SSLSocket, self).__init__(sock._sock, *args, **kwargs)
            # put the _filament.Socket in place (normally the _socket.socket).
            self._sock = self._fil_sock

        def gettimeout(self):
            return self._fil_sock.gettimeout()

        def settimeout(self, val):
            return self._fil_sock.settimeout(val)

        def setblocking(self, val):
            return self._fil_sock.setblocking(val)

        def _get_timeout(self):
            return _fil_io.abstimeout_from_timeout(self.gettimeout())

        def _fil_wait_read(self, to, operation):
            if self._fil_sock.gettimeout() == 0.0:
                # always called within an exception
                raise

            def _fil_read_timeout():
                raise _fil_ssl.SSLError("The %s operation timed out" % (operation,))
            _fil_io.fd_wait_read_ready(self._sock.fileno(), abstimeout=to,
                                       timeout_exc=_fil_read_timeout)

        def _fil_wait_write(self, to, operation):
            if self._fil_sock.gettimeout() == 0.0:
                # always called within an exception
                raise

            def _fil_write_timeout():
                raise _fil_ssl.SSLError("The %s operation timed out" % (operation,))
            _fil_io.fd_wait_write_ready(self._sock.fileno(), abstimeout=to,
                                        timeout_exc=_fil_write_timeout)

        def _real_connect(self, addr, connect_ex):
            # do some tricky stuff, as this needs the _socket.socket as
            # self._sock to create the SSL context, but then it'll also be used
            # for socket.connect() which is always in non-block mode.
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
                    self._fil_wait_write(to, 'handshake')

        def read(self, *args, **kwargs):
            to = self._get_timeout()
            while 1:
                try:
                    return super(SSLSocket, self).read(*args, **kwargs)
                except _fil_ssl.SSLWantReadError:
                    self._fil_wait_read(to, 'read')
                except _fil_ssl.SSLWantWriteError:
                    self._fil_wait_write(to, 'read')

        def write(self, *args, **kwargs):
            to = self._get_timeout()
            while 1:
                try:
                    return super(SSLSocket, self).write(*args, **kwargs)
                except _fil_ssl.SSLWantReadError:
                    self._fil_wait_read(to, 'write')
                except _fil_ssl.SSLWantWriteError:
                    self._fil_wait_write(to, 'write')

        def unwrap(self):
            to = self._get_timeout()
            while 1:
                try:
                    return super(SSLSocket, self).unwrap()
                except _fil_ssl.SSLWantReadError:
                    self._fil_wait_read(to, 'shutdown')
                except _fil_ssl.SSLWantWriteError:
                    self._fil_wait_write(to, 'shutdown')

    # Replacing this in the 'ssl' dict lets SSLContext.wrap_socket() and the
    # _create_default_context() helpers find our cooperative SSLSocket without
    # us having to subclass SSLContext.
    _fil_ssl.SSLSocket = SSLSocket

    def sslwrap_simple(sock, keyfile=None, certfile=None):
        return SSLSocket(sock, keyfile=keyfile, certfile=certfile)

    # Copy the rest of the real ssl.
    _fil_util.copy_globals(_fil_ssl, globals())
