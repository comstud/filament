"""Cooperative replacement for the ``select`` module.

``select.select`` normally blocks the whole OS thread until one of the file
descriptors is ready.  Under filament that would stall every greenthread; this
version instead waits on the descriptors *cooperatively* using filament's IO
thread (``_filament.io.fd_wait_read_ready`` / ``fd_wait_write_ready``), so a
greenthread doing its own IO multiplexing yields instead of blocking.

``select()`` and ``poll()`` are both cooperative; ``poll`` is built on top of
``select`` and carries the stdlib's millisecond timeout and event bitmasks.
urllib3 reaches for ``select.poll()`` on every pooled-connection reuse, so a
missing one takes ``requests`` down with it.  Constants and error types are
copied from the stdlib module.

Importing this module does NOT patch anything; use ``patch_select()``.
"""

import errno as _errno

from filament import _util as _fil_util
from filament import patcher as _fil_patcher
import filament as _fil
import _filament.io as _fil_io
from filament import event as _fil_event
from filament import exc

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

    Implementation notes:

    * ``timeout=0`` means "poll, don't block".  Our IO layer reads a zero
      timeout as "expire immediately" rather than "check readiness now", and
      the stdlib call cannot stall the thread when it is told not to wait, so
      that case goes straight to the pristine ``select.select``.
    * one descriptor is handled inline; several are handled by a helper
      greenthread each, and we wake as soon as the *first* one is ready --
      waiting for them all would turn every multi-fd select into a
      full-timeout stall.  The ready set is then read back with a
      non-blocking stdlib select, so we report every ready descriptor and not
      just the one that woke us.
    * ``xlist`` (exceptional conditions) is reported when the stdlib sees one,
      but we never *wake* on it -- filament's IO layer only distinguishes
      read/write readiness -- so an xlist-only select just runs out its
      timeout.  This matches how most cooperative libraries treat it.
    """
    if timeout is not None and timeout <= 0:
        # Non-blocking: no yielding needed, and no cooperative machinery can
        # express "check right now" anyway.
        return _orig_select.select(rlist, wlist, xlist, 0)

    # Map fd -> original object so we can return exactly what we were given.
    read_objs = {}
    write_objs = {}
    for obj in rlist:
        read_objs[_fileno(obj)] = obj
    for obj in wlist:
        write_objs[_fileno(obj)] = obj

    if not read_objs and not write_objs:
        # Nothing to watch: select() degenerates to a sleep.
        if timeout:
            _fil.sleep(timeout)
        return [], [], []

    # One descriptor is the common case (a socket checking its own readiness);
    # do it in the calling greenthread rather than spawning anything.
    if len(read_objs) + len(write_objs) == 1:
        if read_objs:
            fd, obj = list(read_objs.items())[0]
            waiter, bucket = _fil_io.fd_wait_read_ready, 0
        else:
            fd, obj = list(write_objs.items())[0]
            waiter, bucket = _fil_io.fd_wait_write_ready, 1
        try:
            waiter(fd, timeout=timeout)
        except exc.Timeout as e:
            if type(e) is not exc.Timeout:
                raise          # an outer with-Timeout fired in *us*; propagate
            return [], [], []
        except Exception:
            return [], [], []
        return ([obj], [], []) if bucket == 0 else ([], [obj], [])

    ready = _fil_event.Event()

    # Each helper waits on one fd and wakes us as soon as it is ready.
    def _wait(fd, waiter):
        try:
            waiter(fd, timeout=timeout)
        except (exc.Timeout, Exception):
            # A timeout or error just means "not ready"; select() reports that
            # by omission, so swallow it here.  (exc.Timeout is a BaseException
            # and must be named explicitly.)
            return
        ready.set()

    helpers = []
    for fd in read_objs:
        helpers.append(_fil.spawn(_wait, fd, _fil_io.fd_wait_read_ready))
    for fd in write_objs:
        helpers.append(_fil.spawn(_wait, fd, _fil_io.fd_wait_write_ready))

    try:
        ready.wait(timeout)
    finally:
        # Whether we woke on readiness or on the timeout, no helper may outlive
        # this call -- a leaked waiter would hold its fd (and itself) forever.
        _fil.killall(helpers)

    # We now know something is ready (or that we timed out); ask the stdlib
    # which descriptors, non-blocking, so we report the COMPLETE ready set the
    # way select() is supposed to -- and hand back the caller's own objects.
    return _orig_select.select(rlist, wlist, xlist, 0)


class poll(object):
    """
    Cooperative ``select.poll`` object.

    Registration and the returned ``(fd, eventmask)`` pairs follow the stdlib;
    the wait itself is :func:`select`, so it yields instead of blocking the
    thread.  Timeouts are in milliseconds (``None`` or negative blocks), as the
    stdlib has them -- note that differs from :func:`select`'s seconds.

    ``POLLPRI`` is treated as read interest and never reported on its own;
    filament's IO layer does not distinguish out-of-band data.
    """

    def __init__(self):
        self._registry = {}

    def register(self, fd, eventmask=None):
        if eventmask is None:
            eventmask = POLLIN | POLLPRI | POLLOUT
        self._registry[_fileno(fd)] = eventmask

    def modify(self, fd, eventmask):
        fd = _fileno(fd)
        if fd not in self._registry:
            raise OSError(_errno.ENOENT, 'No such file descriptor')
        self._registry[fd] = eventmask

    def unregister(self, fd):
        # Stdlib raises KeyError for an unregistered descriptor.
        del self._registry[_fileno(fd)]

    def poll(self, timeout=None):
        rlist = [fd for fd, mask in self._registry.items()
                 if mask & (POLLIN | POLLPRI)]
        wlist = [fd for fd, mask in self._registry.items() if mask & POLLOUT]
        seconds = None if timeout is None or timeout < 0 else timeout / 1000.0
        readable, writable, _ = select(rlist, wlist, [], seconds)
        events = {}
        for fd in readable:
            events[fd] = events.get(fd, 0) | POLLIN
        for fd in writable:
            events[fd] = events.get(fd, 0) | POLLOUT
        return list(events.items())


# Copy across constants (POLLIN, etc.) and anything else, but do NOT clobber our
# cooperative ``select``/``poll``/``error`` (copy_globals only fills gaps).
_fil_util.copy_globals(_orig_select, globals())
