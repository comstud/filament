"""Filament sockets.

These should match the normal sockets except that they will cooperate properly
with filaments. Importing this module does NOT monkey patch anything. Anything
using the system socket module will stay functioning as-is, although they won't
be very useful as they will block filaments unless used in a real thread.
"""

from __future__ import absolute_import
import sys

# We have to do a little trickery to work, since we want to (ab)use the system
# 'socket' module to give the same functionality.
#
# Under the covers, the system 'socket' module imports the built-in '_socket'
# module and gets most of its functionality from it. '_filament.socket' is
# roughly the same thing, except that it cooperates with filaments. So, we're
# going to have '_filament.socket' masquerade as '_socket' temporarily while we
# import 'socket' and use 'socket' as ourselves.

# import the filament-aware _socket, which will also cause an import of the
# real _socket if it is not already loaded
import _filament.socket
_orig__socket = sys.modules['_socket']

# Replace _socket
_filament.socket.__name__ = '_socket'
sys.modules['_socket'] = _filament.socket

# Remove the real 'socket' if it's already loaded.
_orig_socket = sys.modules.pop('socket', None)
# Now we can import 'socket' which will grab *our* _socket.
import socket

# Reset things now that we've imported socket
sys.modules['_socket'] = _orig__socket
if _orig_socket:
    sys.modules['socket'] = _orig_socket
else:
    del sys.modules['socket']

del _orig__socket
del _orig_socket

# socket becomes us
socket.__name__ = __name__
socket.__file__ = __file__
sys.modules[__name__] = socket
