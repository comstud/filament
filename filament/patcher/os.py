"""os module replacement that cooperates with Filaments"""

import errno
import fcntl
import stat

from filament import io as _fil_io

_orig_os = __import__('os')
_orig_fdopen = _orig_os.fdopen
_orig_read = _orig_os.read
_orig_write = _orig_os.write

_ORIG_MAP = {}

def _patch():
    if not _ORIG_MAP:
        _ORIG_MAP['os.fdopen'] = _orig_fdopen
        _ORIG_MAP['os.read'] = _orig_read
        _ORIG_MAP['os.write'] = _orig_write
        _orig_os.fdopen = fdopen
        _orig_os.read = _fil_io.os_read
        _orig_os.write = _fil_io.os_write
    return _ORIG_MAP


class NBFile(object):
    def __init__(self, f):
        self._orig_f = f
        self._act_nonblocking = True
        self._blocking_errnos = [errno.EAGAIN]
        wb = getattr(errno, 'EWOULDBLOCK', None)
        if wb:
            self._blocking_errnos.append(wb)

    def __getattr__(self, key):
        return getattr(self._orig_f, key)

    def fileno(self):
        fd = _fil_io.FDesc(self._orig_f.fileno())
        fd._fil_sock = self
        return fd

    def read(self, size=None):
        try:
            if size is None:
                return self._orig_f.read()
            else:
                return self._orig_f.read(size)
        except (OSError, IOError) as e:
            if self._act_nonblocking:
                raise
            if e.errno not in self._blocking_errnos:
                raise
        _fil_io.fd_wait_read_ready(self.fileno())
        raise NotImplemented()

    def write(self, s):
        try:
            return self._orig_f.write(s)
        except (OSError, IOError) as e:
            if self._act_nonblocking:
                raise
            if e.errno not in self._blocking_errnos:
                raise
        _fil_io.fd_wait_write_ready(self.fileno())
        raise NotImplemented()


def fdopen(fd, *args, **kwargs):
    f = NBFile(_orig_fdopen(fd, *args, **kwargs))
    try:
        f._act_nonblocking = fd._fil_sock._act_nonblocking
        return f
    except AttributeError:
        pass

    # If it's already non-blocking, assume we should act non-blocking
    orig_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    if orig_flags & _orig_os.O_NONBLOCK:
        f._act_nonblocking = True
        return f

    # Only try to set fifos/sockets to non-blocking
    try:
        s = _orig_os.fstat(fd)
    except Exception:
        f._act_nonblocking = True
        return f

    if stat.S_IFMT(s.st_mode) & (stat.S_IFIFO|stat.S_IFSOCK|stat.S_IFCHR):
        new_flags = orig_flags | _orig_os.O_NONBLOCK
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, new_flags)
            f._act_nonblocking = False
        except Exception:
            f._act_nonblocking = True
    else:
        f._act_nonblocking = True
    return f
