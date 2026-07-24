# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.hub
==========================

Drop-in-ish replacement for ``gevent.hub`` (injected as
``sys.modules['gevent.hub']``).

gevent's hub is its event-loop greenlet.  filament runs its own C scheduler and
does not expose a matching object, so this module is a lightweight compatibility
layer.  ``sleep`` and ``get_hub().threadpool`` are faithful; the rest of the
``Hub`` / ``Waiter`` surface is a documented stub exposing only the members
common code touches.
"""

from __future__ import absolute_import

import greenlet

import filament
from filament.gevent_compat import threadpool as _threadpool

# gevent re-exports these from the hub; keep them available here too.
GreenletExit = filament.GreenletExit


def sleep(seconds=0):
    """Faithful mapping onto filament.sleep (gevent.hub.sleep)."""
    return filament.sleep(seconds)


class Waiter(object):
    """
    Minimal stand-in for ``gevent.hub.Waiter`` -- a one-shot switch/get pair.

    Backed by :class:`filament.AsyncResult`: ``switch(value)`` delivers a value,
    ``switch_exception`` delivers an exception, and ``get()`` blocks for it.
    This covers the common "park a greenlet, wake it later" use; the deeper
    hub-internal Waiter API is not modelled (documented stub).
    """

    def __init__(self, hub=None):
        self._result = filament.AsyncResult()

    def switch(self, value=None):
        """Wake the waiter with ``value``."""
        self._result.set(value)

    def switch_args(self, *args):
        """Wake the waiter with a tuple of values (gevent convenience)."""
        self._result.set(args)

    def throw(self, *args):
        """Wake the waiter by raising an exception in ``get()``."""
        exc = args[0] if args else Exception
        if isinstance(exc, type):
            exc = exc(*args[1:])
        self._result.set_exception(exc)

    def get(self):
        """Block until switched; return the value or re-raise the exception."""
        return self._result.get()

    def ready(self):
        return self._result.ready()


class Hub(object):
    """
    Lightweight stand-in for ``gevent.hub.Hub``.

    Exposes the handful of attributes third-party code commonly reads:

      * ``.threadpool`` -- a real, working :class:`ThreadPool` (faithful).
      * ``.loop``       -- a stub loop object (see :class:`_Loop`).
      * ``.switch()``   -- yields to filament's scheduler (closest analog to
                            switching into gevent's hub).
      * ``.greenlet``   -- the current greenlet (there is no dedicated hub
                            greenlet to hand out -- documented stub).
    """

    def __init__(self):
        self._threadpool = None
        self.loop = _Loop()

    @property
    def threadpool(self):
        # Created lazily; a genuine filament-backed thread pool.
        if self._threadpool is None:
            self._threadpool = _threadpool.ThreadPool()
        return self._threadpool

    @property
    def greenlet(self):
        # STUB: no dedicated hub greenlet exists under filament.
        return greenlet.getcurrent()

    def switch(self):
        # Faithful-ish: yield to the scheduler.
        return filament.sleep(0)

    def sleep(self, seconds=0):
        return filament.sleep(seconds)

    def spawn(self, func, *args, **kwargs):
        return filament.spawn(func, *args, **kwargs)


class _Loop(object):
    """
    Stub for gevent's libev/libuv ``loop`` object.

    Only the ``run_callback`` scheduling entry point is provided (mapped onto a
    zero-delay filament spawn).  Timers/watchers are NOT modelled -- deep loop
    introspection is out of scope for the shim.
    """

    def run_callback(self, func, *args):
        # Fire-and-forget on the next scheduler turn.
        filament.spawn_n(func, *args)
        return None


# Process-wide singleton hub (gevent's get_hub is likewise per-thread singleton;
# filament is single-scheduler so one instance suffices for the common case).
_the_hub = Hub()


def get_hub():
    """Return the (singleton) compatibility hub.  See :class:`Hub`."""
    return _the_hub


def get_hub_if_exists():
    """gevent helper: return the hub (always exists here)."""
    return _the_hub


__all__ = ["Hub", "Waiter", "get_hub", "sleep", "GreenletExit"]
