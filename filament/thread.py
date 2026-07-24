"""Cooperative replacement for the low-level thread module.

On Python 3 the stdlib low-level thread module is ``_thread``; on Python 2 it
is ``thread``.  This module greens whichever one applies:

* ``start_new_thread`` spawns a filament greenthread instead of an OS thread.
* ``allocate_lock`` / ``LockType`` hand back a cooperative lock whose API
  matches the historic ``thread.lock`` (``acquire(waitflag)``, ``locked()``,
  ``acquire_lock``/``release_lock``).
* ``get_ident`` returns a unique-per-greenthread identity (so code that keys
  data off the "thread id" gets one id per filament, which is what cooperative
  code expects).

Anything we don't override is copied verbatim from the original module.
"""

import sys

import filament as _fil
from filament import _util as _fil_util
from filament import patcher as _fil_patcher
import _filament.locking as _fil_locking


_PY3 = sys.version_info[0] >= 3

# Tell the patcher which stdlib module we stand in for.
__filament__ = {'patch': '_thread' if _PY3 else 'thread'}

# Grab the pristine original so we can copy across everything we don't
# explicitly override (errors, stack-size helpers, TIMEOUT_MAX, etc.).
_orig_thread = _fil_patcher.get_original('_thread' if _PY3 else 'thread')


class LockType(_fil_locking.Lock):
    """A cooperative lock exposing the old low-level ``thread.lock`` API.

    filament's C lock uses ``acquire(blocking=...)``; the low-level thread
    module historically used ``acquire(waitflag)`` and also exposed
    ``acquire_lock``/``release_lock``/``locked_lock`` aliases.  We adapt.
    """

    def acquire(self, waitflag=1, timeout=-1):
        # waitflag: 1 (block) / 0 (non-blocking).  ``timeout`` is accepted for
        # Py3 parity; -1 means "no timeout".
        blocking = bool(waitflag)
        if timeout is not None and timeout != -1:
            return super(LockType, self).acquire(blocking=blocking,
                                                 timeout=timeout)
        return super(LockType, self).acquire(blocking=blocking)

    # Historic aliases from the Python 2 ``thread`` module.
    def acquire_lock(self, waitflag=1):
        return self.acquire(waitflag)

    def release_lock(self):
        return self.release()

    def locked(self):
        # There is no non-blocking "peek" primitive, so probe by trying a
        # non-blocking acquire and immediately releasing on success.
        got = self.acquire(0)
        if got:
            self.release()
        return not got

    def locked_lock(self):
        return self.locked()


# The classic factory name.
def allocate_lock():
    return LockType()


# Some code refers to ``allocate`` (very old alias).
allocate = allocate_lock


def get_ident():
    """Return a unique identity for the current greenthread.

    The low-level contract is only that the value is a non-zero integer that is
    unique among simultaneously-running "threads" and may be recycled once a
    thread exits.  ``id()`` of the current filament greenlet satisfies exactly
    that: it is unique among live greenlets and may be reused after one dies.
    This gives cooperative code (e.g. ``threading.local``, logging) a distinct
    id per filament rather than a single shared OS-thread id.
    """
    return id(_fil.Filament.getcurrent())


def start_new_thread(fn, args=(), kwargs=None):
    """Spawn a filament greenthread and return its identity.

    Mirrors ``_thread.start_new_thread(function, args[, kwargs])``: ``args`` is
    a tuple and ``kwargs`` an optional dict.
    """
    if kwargs is None:
        kwargs = {}
    fil = _fil.spawn(fn, *args, **kwargs)
    # The stdlib returns an integer ident; hand back the greenlet's identity so
    # it lines up with get_ident() semantics.
    return id(fil)


# Py2 spelled it ``start_new`` as well.
start_new = start_new_thread


# Copy across everything we did not override (error, LockType already set,
# interrupt_main, stack_size, TIMEOUT_MAX, get_native_id, ...).
_fil_util.copy_globals(_orig_thread, globals())
