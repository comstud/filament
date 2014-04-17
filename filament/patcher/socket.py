"""_socket replacement that cooperates with Filaments"""

from filament import io as _fil_io

import sys
import _socket as _orig_socket

from _socket import *

_ORIG_MAP = {}

def _patch():
    if not _ORIG_MAP:
        _ORIG_MAP['_socket'] = _orig_socket
        sys.modules['_socket'] = sys.modules[__name__]
    return _ORIG_MAP


class NBSocket(_orig_socket.socket):
    def __init__(self, *args, **kwargs):
        super(NBSocket, self).__init__(*args, **kwargs)
        self._act_nonblocking = False
        super(NBSocket, self).setblocking(0)

    def fileno(self):
        fn = super(NBSocket, self).fileno()
        # Wrapper around the file descriptor so that our patched
        # os module calls can check if the caller expects the
        # call to act non-blocking or not.
        fd = _fil_io.FDesc(fn)
        fd._fil_sock = self
        return fd

    def setblocking(self, opt):
        self._act_nonblocking = (opt == 0)


socket = NBSocket
