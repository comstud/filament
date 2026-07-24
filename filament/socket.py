"""Filament sockets.

These should match the normal sockets except that they will cooperate properly
with filaments. Importing this module does NOT monkey patch anything. Anything
using the system socket module will stay functioning as-is, although they won't
be very useful as they will block filaments unless used in a real thread.
"""

# We have to do a little trickery to work, since we want to (ab)use the system
# 'socket' module to give the same functionality.
#
# Under the covers, the system 'socket' module imports the built-in '_socket'
# module and gets most of its functionality from it. On Python 3 the high-level
# 'socket.socket' class is a *subclass* of the low-level '_socket.socket', and
# defers nearly all of its real work (recv/send/connect/bind/listen/_accept/...)
# to that low-level base. On Python 2 the high-level 'socket._socketobject'
# instead *wraps* a '_socket.socket' instance stored in 'self._sock'.
#
# '_filament.socket' is roughly the same thing as '_socket', except that its
# Socket type cooperates with filaments: the blocking operations yield the
# current greenthread to the scheduler (via the libevent io thread) instead of
# blocking the whole process. So the trick is to make '_filament.socket'
# masquerade as '_socket' *temporarily* while we import a fresh, private copy of
# the stdlib 'socket' module. That private copy then binds its high-level
# 'socket' class on top of filament's cooperative Socket -- on Python 3 by
# subclassing it, on Python 2 by wrapping it -- and everything else (constants,
# create_connection(), socketpair(), getaddrinfo(), ...) comes along for free.
#
# The masquerade is scoped to the 'with' block below: ModuleReplacer swaps
# '_socket' out of sys.modules for '_filament.socket', imports our own copy of
# 'socket', and then restores sys.modules so the *real* 'socket' / '_socket'
# modules the rest of the program sees are left completely untouched.

import sys as _sys

from filament import _util as _fil_util
from _filament import socket as _fil__socket

# The greening/patch machinery keys off this marker to know that importing this
# module is how you obtain a filament-cooperative replacement for 'socket'.
__filament__ = {"patch": "socket"}

with _fil_util.ModuleReplacer([('_socket', _fil__socket)]):
    # copy_module imports a *fresh* copy of stdlib 'socket' (while the
    # masquerade above is in effect) and returns it without leaving it in
    # sys.modules; copy_globals then lifts all of its public names into this
    # module's namespace. Because '_socket' resolved to '_filament.socket' at
    # import time, the 'socket' class (and helpers built on it) are all bound to
    # filament's cooperative Socket.
    _fil_util.copy_globals(_fil_util.copy_module('socket'), globals())

# On Python 2 the stdlib 'socket' module defines '_realsocket' as an alias of
# '_socket.socket'; on Python 3 it does not exist. Filament (and its tests)
# expect 'filament.socket._realsocket' to point at the underlying cooperative
# socket type, mirroring the stdlib-2 contract regardless of Python version.
# Under the masquerade that underlying type is '_filament.socket.socket'.
_realsocket = _fil__socket.socket

# On Python 2 the high-level 'socket._socketobject.dup()' returns a new wrapper
# that *shares* the same underlying '_sock' (and therefore the same fd) -- a
# well-known Py2-vs-Py3 divergence (Py3's socket.dup() dup(2)s the fd into an
# independent socket).  filament's cooperative low-level socket implements a
# real dup() (a genuine fd duplication), so route the high-level dup() through
# it on Py2 to match Py3 semantics: an independent socket with a distinct fd.
if _sys.version_info < (3, 0):
    def _fil_socket_dup(self):
        """dup() -> socket object

        Return a new socket object connected to the same system resource,
        backed by an independently dup(2)'d file descriptor.
        """
        return socket(_sock=self._sock.dup())  # noqa: F821 (bound by copy_globals)

    socket.dup = _fil_socket_dup  # noqa: F821 (bound by copy_globals)
