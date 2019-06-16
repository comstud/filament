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
# module and gets most of its functionality from it. '_filament.socket' is
# roughly the same thing, except that it cooperates with filaments. So, we're
# going to have '_filament.socket' masquerade as '_socket' temporarily while we
# import a copy of 'socket' and copy it in.

from filament import _util as _fil_util
from _filament import socket as _fil__socket

__filament__ = {"patch": "socket"}

with _fil_util.ModuleReplacer([('_socket', _fil__socket)]):
    _fil_util.copy_globals(_fil_util.copy_module('socket'), globals())
