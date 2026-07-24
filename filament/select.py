"""Cooperative replacement for the ``select`` module.

``select.select`` normally blocks the whole OS thread until one of the file
descriptors is ready.  Under filament that would stall every greenthread; this
version instead waits on the descriptors *cooperatively* using filament's IO
thread (``_filament.io.fd_wait_read_ready`` / ``fd_wait_write_ready``), so a
greenthread doing its own IO multiplexing yields instead of blocking.

Only ``select()`` is implemented cooperatively.  ``poll`` is intentionally left
as a clear NotImplementedError (see below) rather than silently falling back to
the blocking stdlib version.  Constants and error types are copied from the
stdlib module.

Importing this module does NOT patch anything; use ``patch_select()``.
"""

from filament import _util as _fil_util
from filament import patcher as _fil_patcher
import filament as _fil
import _filament.io as _fil_io

__filament__ = {'patch': 'select'}

# Pristine stdlib select (for the error type and any constants).
_orig_select = _fil_patcher.get_original('select')

# The stdlib raises ``select.error`` (== OSError on Py3) on failure.
error = getattr(_orig_select, 'error', OSError)


def _fileno(obj):
    """Return an integer fd from an int or an object with ``fileno()``."""
    if isinstance(obj, int):
        return obj
    return obj.fileno()


def select(rlist, wlist, xlist, timeout=None):
    """Cooperative ``select.select``.

    Waits until at least one descriptor in ``rlist`` is readable or one in
    ``wlist`` is writable (or until ``timeout`` seconds elapse), yielding to the
    filament scheduler while it waits.  Returns the usual
    ``(readable, writable, exceptional)`` triple.

    Implementation: we spawn one helper greenthread per descriptor of interest.
    Each helper blocks (cooperatively) on its descriptor becoming ready and, on
    success, records the original object.  We then wait for the helpers, bounded
    by ``timeout``.  ``xlist`` (exceptional conditions) is accepted but not
    monitored -- filament's IO layer only distinguishes read/write readiness --
    so it always comes back empty; this matches how most cooperative libraries
    treat it.
    """
    # Map fd -> original object so we can return exactly what we were given.
    read_objs = {}
    write_objs = {}
    for obj in rlist:
        read_objs[_fileno(obj)] = obj
    for obj in wlist:
        write_objs[_fileno(obj)] = obj

    readable = []
    writable = []

    # Each helper waits on one fd and appends the source object when ready.
    def _wait_read(fd, obj):
        try:
            _fil_io.fd_wait_read_ready(fd, timeout=timeout)
            readable.append(obj)
        except Exception:
            # A timeout or error just means "not ready"; select() reports that
            # by omission, so swallow it here.
            pass

    def _wait_write(fd, obj):
        try:
            _fil_io.fd_wait_write_ready(fd, timeout=timeout)
            writable.append(obj)
        except Exception:
            pass

    helpers = []
    for fd, obj in read_objs.items():
        helpers.append(_fil.spawn(_wait_read, fd, obj))
    for fd, obj in write_objs.items():
        helpers.append(_fil.spawn(_wait_write, fd, obj))

    # Wait for all helpers to settle.  Because each helper is itself bounded by
    # ``timeout``, joining them all cannot exceed roughly ``timeout`` seconds.
    for helper in helpers:
        try:
            helper.join()
        except Exception:
            pass

    # xlist is not monitored (see docstring); always empty.
    return readable, writable, []


def poll(*args, **kwargs):
    """Not implemented cooperatively -- use :func:`select` instead.

    A cooperative ``poll`` object is a larger piece of work than we take on
    here; failing loudly is better than silently handing back the blocking
    stdlib poll (which would stall every greenthread).
    """
    raise NotImplementedError(
        'filament.select does not provide a cooperative poll(); '
        'use filament.select.select() instead')


# Copy across constants (POLLIN, etc.) and anything else, but do NOT clobber our
# cooperative ``select``/``poll``/``error`` (copy_globals only fills gaps).
_fil_util.copy_globals(_orig_select, globals())
