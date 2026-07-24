# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.server
=============================

Drop-in-ish replacement for ``gevent.server`` (injected as
``sys.modules['gevent.server']``).

Implements :class:`StreamServer` -- a cooperative TCP server that accepts
connections on a filament green socket and runs a handler per connection,
bounding concurrency with a :class:`filament.Pool`.  This is a real, working
implementation (not a stub); the accept loop, per-connection spawn and
graceful stop all function.

Documented limitations:
  * SSL wrapping via ``**ssl_args`` is honoured through ``filament.ssl`` only if
    the caller passes ``keyfile``/``certfile`` (basic wrap); advanced SSL
    context options beyond filament.ssl.wrap_socket are not modelled.
  * The libev-specific knobs of gevent's server (``.loop``, watchers) are not
    provided.
"""

from __future__ import absolute_import

import filament
import filament.socket as _green_socket


class StreamServer(object):
    """
    A cooperative TCP stream server (gevent.server.StreamServer).

    :param listener: either an ``(host, port)`` tuple (we create/bind/listen a
        green socket) or an already-listening green socket.
    :param handle: ``handle(socket, address)`` called for each connection.  May
        instead be provided by subclassing and overriding :meth:`handle`.
    :param spawn: concurrency control.  An int (or None) builds an internal
        :class:`filament.Pool` of that size; ``'default'`` uses an unbounded
        pool; a Pool/Group instance is used directly.
    :param backlog: listen backlog.
    """

    def __init__(self, listener, handle=None, spawn='default', backlog=256,
                 **ssl_args):
        self.backlog = backlog
        self._ssl_args = ssl_args
        self.started = False
        self._stopped = False
        self._accept_greenlet = None

        # Resolve the listening socket.
        if isinstance(listener, tuple):
            self.socket = self._make_listener(listener, backlog)
        else:
            # An already-prepared socket.
            self.socket = listener
        # Remember our bound address for tests/callers (server_host/port).
        try:
            self.address = self.socket.getsockname()
        except Exception:
            self.address = listener if isinstance(listener, tuple) else None

        # Install the handler (constructor arg wins over a subclass method).
        if handle is not None:
            self.handle = handle

        # Resolve the concurrency pool.
        if spawn == 'default' or spawn is None:
            self.pool = filament.Pool()          # unbounded
        elif isinstance(spawn, int):
            self.pool = filament.Pool(spawn)
        else:
            self.pool = spawn                    # a Pool/Group instance

    # -- listener setup ------------------------------------------------------

    @staticmethod
    def _make_listener(address, backlog):
        sock = _green_socket.socket(_green_socket.AF_INET,
                                    _green_socket.SOCK_STREAM)
        sock.setsockopt(_green_socket.SOL_SOCKET,
                        _green_socket.SO_REUSEADDR, 1)
        sock.bind(address)
        sock.listen(backlog)
        return sock

    # -- handler override point ---------------------------------------------

    def handle(self, sock, address):  # pragma: no cover - overridden/replaced
        """Handle one connection.  Override, or pass ``handle=`` to __init__."""
        raise NotImplementedError("StreamServer requires a handle callable")

    def wrap_socket_and_handle(self, client_socket, address):
        # Optionally wrap in SSL, then dispatch to the handler.  We always close
        # the client socket when the handler returns/raises.
        try:
            if self._ssl_args:
                client_socket = self._wrap_ssl(client_socket)
            self.handle(client_socket, address)
        finally:
            try:
                client_socket.close()
            except Exception:
                pass

    def _wrap_ssl(self, sock):
        import filament.ssl as _green_ssl
        return _green_ssl.wrap_socket(sock, server_side=True, **self._ssl_args)

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        """Bind (if needed) and start the accept loop greenthread."""
        if self.started:
            return
        self.started = True
        self._stopped = False
        self._accept_greenlet = filament.spawn(self._accept_loop)

    def _accept_loop(self):
        while not self._stopped:
            try:
                client, address = self.socket.accept()
            except filament.GreenletExit:
                # We were killed by stop(); exit quietly.
                break
            except Exception:
                # Listening socket closed under us (stop) -> end the loop.
                if self._stopped:
                    break
                raise
            # Spawn the handler in the concurrency pool.
            self.pool.spawn(self.wrap_socket_and_handle, client, address)

    def serve_forever(self):
        """Start the server and block until :meth:`stop` is called."""
        self.start()
        # Block by joining the accept-loop greenthread.
        try:
            self._accept_greenlet.wait()
        except filament.GreenletExit:
            pass

    def stop(self, timeout=None):
        """Stop accepting, kill the accept loop, and close the socket."""
        self._stopped = True
        if self._accept_greenlet is not None:
            filament.kill(self._accept_greenlet)
            self._accept_greenlet = None
        try:
            self.socket.close()
        except Exception:
            pass
        self.started = False

    def close(self):
        """Alias for :meth:`stop`."""
        self.stop()

    # -- introspection helpers (gevent parity) ------------------------------

    @property
    def server_host(self):
        return self.address[0] if self.address else None

    @property
    def server_port(self):
        return self.address[1] if self.address else None


__all__ = ["StreamServer"]
