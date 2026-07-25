# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.eventlet_compat.hubs
=============================

Drop-in-ish replacement for ``eventlet.hubs`` (injected as
``sys.modules['eventlet.hubs']``).

eventlet's "hub" is its event-loop object.  filament has an equivalent concept
-- the C scheduler -- but does not expose a matching public object, so most of
this module is a thin compatibility layer:

  * :func:`trampoline` is a FAITHFUL mapping: it blocks the current greenthread
    until a file descriptor is read/write ready (or a timeout fires), using
    filament's cooperative ``filament.io.fd_wait_*_ready`` primitives -- exactly
    what eventlet's trampoline does.
  * :func:`get_hub` / :func:`use_hub` return / accept a lightweight ``_Hub``
    STUB that exposes the handful of attributes common eventlet code touches
    (``.greenlet``, ``.switch()``, ``.schedule_call_global``).  It is NOT a full
    reactor; deep hub internals are intentionally unimplemented.
"""

from __future__ import absolute_import

try:
    # Python 3: filament's private vendored greenlet runtime.  All of
    # filament's switching happens on this runtime, so getcurrent() /
    # GreenletExit must come from it, not from an installed greenlet.
    import _fil_greenlet as greenlet
except ImportError:  # Python 2 / stock-greenlet build
    import greenlet

import filament
from filament import io as _fil_io


class _Hub(object):
    """
    Minimal stand-in for eventlet's hub object.

    Only the members commonly referenced by third-party code are provided; each
    is documented as a faithful mapping or a stub.  filament runs its own C
    scheduler, so there is no real single "hub greenlet" to hand out -- we
    return the current greenlet's context where eventlet would return the hub.
    """

    @property
    def greenlet(self):
        # STUB: eventlet code sometimes compares against ``hub.greenlet`` to
        # detect "am I in the hub?".  We hand back the current greenlet; there
        # is no dedicated hub greenlet to expose.
        return greenlet.getcurrent()

    def switch(self):
        # Faithful-ish: yielding to filament's scheduler is the closest analog
        # to switching into eventlet's hub.
        return filament.sleep(0)

    def schedule_call_global(self, seconds, function, *args, **kwargs):
        # Faithful mapping onto filament's delayed spawn; returns a handle with
        # ``.cancel()``.
        return filament.spawn_later(seconds, function, *args, **kwargs)

    # eventlet also spells the local variant this way.
    schedule_call_local = schedule_call_global

    def add_timer(self, timer):  # pragma: no cover - rarely used externally
        # STUB: eventlet Timer objects are not modelled here.
        raise NotImplementedError("eventlet_compat hub does not model Timer "
                                  "objects; use filament.spawn_later")


# One process-wide hub instance (eventlet's get_hub is likewise a singleton).
_the_hub = _Hub()


def get_hub():
    """Return the (singleton) compatibility hub.  See :class:`_Hub`."""
    return _the_hub


def use_hub(hub=None):
    """
    eventlet.hubs.use_hub -- selecting a hub implementation.

    STUB: filament always uses its own C scheduler, so hub selection is a no-op
    accepted for API compatibility.
    """
    return None


def trampoline(fd, read=None, write=None, timeout=None,
               timeout_exc=None, mark_as_closed=None):
    """
    Block the current greenthread until ``fd`` is ready (eventlet.trampoline).

    FAITHFUL mapping onto filament's cooperative fd-wait primitives:

    :param fd: a file descriptor (int) or an object with ``fileno()``.
    :param read: wait for readability if truthy.
    :param write: wait for writability if truthy.
    :param timeout: seconds to wait before raising ``timeout_exc``.
    :param timeout_exc: exception raised on timeout (defaults to filament's
        Timeout).

    Exactly one of ``read``/``write`` should be truthy, matching eventlet.
    """
    # Resolve to a raw integer fileno.
    fileno = fd if isinstance(fd, int) else fd.fileno()

    # Convert the relative timeout into the absolute deadline object the C
    # fd_wait_* helpers expect (None -> no deadline).
    abstimeout = _fil_io.abstimeout_from_timeout(timeout)
    if timeout_exc is None:
        timeout_exc = filament.Timeout

    if read:
        return _fil_io.fd_wait_read_ready(fileno, abstimeout=abstimeout,
                                          timeout_exc=timeout_exc)
    if write:
        return _fil_io.fd_wait_write_ready(fileno, abstimeout=abstimeout,
                                           timeout_exc=timeout_exc)
    # Neither requested: nothing to wait for.
    return None


__all__ = ["get_hub", "use_hub", "trampoline"]
