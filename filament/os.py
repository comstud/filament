"""Cooperative pieces of the ``os`` module.

We do NOT replace the whole ``os`` module -- it has hundreds of names we must
leave untouched.  Instead this module exposes cooperative versions of a few IO
entry points and declares an *item-level* patch marker so the patcher swaps
only those names onto the real ``os`` module:

* ``os.read`` / ``os.write`` -> ``_filament.io.os_read`` / ``os_write``, which
  wait cooperatively for the fd to become ready instead of blocking the OS
  thread.
* ``os.fdopen`` -> a wrapper that returns a non-blocking-aware file object.

Not greened (documented limitations)
------------------------------------
``os.fork`` and ``os.waitpid`` are *not* patched here.  Forking a process that
is running greenthreads is fraught (the child inherits only the forking
greenthread and a half-initialised scheduler/IO thread), and a cooperative
``waitpid`` belongs with the ``subprocess`` greening.  See
``filament/subprocess.py`` for cooperative child-process waiting.

Importing this module does NOT patch anything; use ``patcher.patch_os()``.
"""

# NB: required on py2 -- without it "import os" here resolves to THIS module
# (implicit relative import), leaving _orig_os pointing at filament.os itself.
from __future__ import absolute_import

import errno
import fcntl
import os as _orig_os
import stat

from filament import io as _fil_io
from filament import patcher as _fil_patcher


# The patcher reads this marker: because an ``items`` list is present it does
# item-level ``setattr`` on the real ``os`` module rather than replacing it.
__filament__ = {'patch': 'os', 'items': ['read', 'write', 'fdopen']}


# ``os.read`` / ``os.write`` map straight onto the cooperative C helpers.  These
# accept an integer fd and yield to the scheduler when the fd would block.
read = _fil_io.os_read
write = _fil_io.os_write


class NBFile(object):
    """A thin wrapper making a file object cooperate with filaments.

    If the underlying fd is non-blocking, a would-block error from a plain
    read/write is turned into a cooperative wait-for-ready followed by a retry,
    so the greenthread yields instead of spinning or blocking.
    """

    def __init__(self, f):
        self._orig_f = f
        # ``_act_nonblocking`` is True when the fd is in non-blocking mode and
        # we therefore need to handle EAGAIN by waiting cooperatively.
        self._act_nonblocking = True
        self._blocking_errnos = [errno.EAGAIN]
        wb = getattr(errno, 'EWOULDBLOCK', None)
        if wb is not None:
            self._blocking_errnos.append(wb)

    def __getattr__(self, key):
        # Delegate anything we don't override to the wrapped file object.
        return getattr(self._orig_f, key)

    def fileno(self):
        fd = _fil_io.FDesc(self._orig_f.fileno())
        fd._fil_sock = self
        return fd

    def _raw_fileno(self):
        return self._orig_f.fileno()

    def read(self, size=-1):
        # Loop: attempt the read; if it would block, wait cooperatively for the
        # fd to become readable and try again.
        while True:
            try:
                if size is None or size < 0:
                    return self._orig_f.read()
                return self._orig_f.read(size)
            except (OSError, IOError) as e:
                if not self._act_nonblocking:
                    raise
                if e.errno not in self._blocking_errnos:
                    raise
            _fil_io.fd_wait_read_ready(self._raw_fileno())

    def write(self, data):
        # Loop until the whole write goes through, waiting cooperatively when
        # the fd would block.
        while True:
            try:
                return self._orig_f.write(data)
            except (OSError, IOError) as e:
                if not self._act_nonblocking:
                    raise
                if e.errno not in self._blocking_errnos:
                    raise
            _fil_io.fd_wait_write_ready(self._raw_fileno())


def fdopen(fd, *args, **kwargs):
    """Cooperative ``os.fdopen``: wrap the resulting file in an ``NBFile``.

    We put fifos/sockets/char devices into non-blocking mode so IO on them
    cooperates; regular files stay blocking (they don't benefit and would just
    add overhead).
    """
    # Use the *original* os.fdopen (in case os has been item-patched).
    orig_fdopen = _fil_patcher.get_original('os', 'fdopen')
    f = NBFile(orig_fdopen(fd, *args, **kwargs))

    # If we were handed a filament FDesc that already knows its blocking mode,
    # inherit it.
    try:
        f._act_nonblocking = fd._fil_sock._act_nonblocking
        return f
    except AttributeError:
        pass

    int_fd = fd if isinstance(fd, int) else fd.fileno()

    # If it's already non-blocking, act non-blocking.
    orig_flags = fcntl.fcntl(int_fd, fcntl.F_GETFL)
    if orig_flags & _orig_os.O_NONBLOCK:
        f._act_nonblocking = True
        return f

    # Only bother flipping fifos/sockets/char devices to non-blocking.
    try:
        st = _orig_os.fstat(int_fd)
    except Exception:
        f._act_nonblocking = True
        return f

    if stat.S_IFMT(st.st_mode) & (stat.S_IFIFO | stat.S_IFSOCK | stat.S_IFCHR):
        try:
            fcntl.fcntl(int_fd, fcntl.F_SETFL, orig_flags | _orig_os.O_NONBLOCK)
            f._act_nonblocking = False
        except Exception:
            f._act_nonblocking = True
    else:
        f._act_nonblocking = True
    return f
