"""_socket replacement that cooperates with Filaments."""

import socket as _orig_socket

from filament import io as _fil_io
from filament import patcher


def _patch():
    patcher.patch('_socket.socket', NBSocket)


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
