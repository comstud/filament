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

try:
    # Python 3: filament's private vendored greenlet runtime.  All of
    # filament's switching happens on this runtime, so getcurrent() /
    # GreenletExit must come from it, not from an installed greenlet.
    import _fil_greenlet as greenlet
except ImportError:  # Python 2 / stock-greenlet build
    import greenlet

import filament
import _filament.io as _fil_io
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
        """
        Wake the waiter by raising an exception in ``get()``.

        Follows ``greenlet.throw`` semantics like gevent's Waiter:
        ``throw()`` raises GreenletExit; ``throw(instance)`` raises it as-is;
        ``throw(type[, value[, tb]])`` raises ``value`` (normalized).
        """
        if not args:
            exc_type, exc_value, exc_tb = GreenletExit, GreenletExit(), None
        elif isinstance(args[0], type):
            exc_type = args[0]
            value = args[1] if len(args) > 1 else None
            if isinstance(value, BaseException):
                exc_value = value
            elif value is None:
                exc_value = exc_type()
            elif isinstance(value, tuple):
                exc_value = exc_type(*value)
            else:
                exc_value = exc_type(value)
            exc_tb = args[2] if len(args) > 2 else None
        else:
            exc_value = args[0]
            exc_type = type(exc_value)
            exc_tb = args[1] if len(args) > 1 else None
        self._result.set_exception(exc_value, (exc_type, exc_value, exc_tb))

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


class _IOWatcher(object):
    """
    An fd-readiness watcher shaped like gevent's ``loop.io()`` result.

    ``start(callback)`` arms it; the callback then runs every time the
    descriptor is ready, until ``stop()``.  Backed by a greenthread parked on
    filament's IO layer rather than a libev watcher, but the contract callers
    rely on -- level-triggered, repeating, cancellable -- is the same.

    pyzmq's ``zmq.green`` builds its entire gevent integration on this: a read
    watcher on the ZMQ socket's FD, whose callback republishes the socket's
    events.  Without it, ``import zmq.green`` falls through to a gevent<1.0
    path and dies.
    """

    # gevent's event mask: 1 = read, 2 = write.
    READ = 1
    WRITE = 2

    def __init__(self, fd, events, ref=True, priority=None):
        self.fd = fd
        self.events = events
        self.callback = None
        self.args = ()
        self._greenthread = None
        self._stopped = False

    @property
    def active(self):
        return self._greenthread is not None

    def start(self, callback, *args, **kwargs):
        """Arm the watcher.  ``pass_events`` is accepted for gevent parity."""
        self.stop()
        self.callback = callback
        self.args = args
        self._stopped = False
        self._greenthread = filament.spawn(self._watch,
                                           kwargs.get("pass_events", False))

    def _watch(self, pass_events):
        wait_read = bool(self.events & self.READ)
        while not self._stopped:
            try:
                if wait_read:
                    _fil_io.fd_wait_read_ready(self.fd)
                else:
                    _fil_io.fd_wait_write_ready(self.fd)
            except filament.GreenletExit:
                return
            except Exception:
                # The descriptor went away (socket closed under us): a libev
                # watcher would simply stop firing, so do the same.
                return
            if self._stopped:
                return
            callback = self.callback
            if callback is None:
                return
            if pass_events:
                callback(self.events, *self.args)
            else:
                callback(*self.args)
            # The callback is expected to consume whatever made the descriptor
            # ready; yield anyway so a callback that does not cannot monopolise
            # the scheduler.
            filament.sleep(0)

    def stop(self):
        self._stopped = True
        greenthread, self._greenthread = self._greenthread, None
        if greenthread is not None:
            filament.kill(greenthread)
        self.callback = None
        self.args = ()

    def close(self):
        self.stop()

    # gevent<1.0 spelling, still called by some libraries.
    cancel = stop


class _Loop(object):
    """
    Stand-in for gevent's libev/libuv ``loop`` object.

    Provides the two entry points third-party code actually uses: callback
    scheduling (``run_callback``) and fd watchers (``io``).  Timers and the
    deeper loop introspection are NOT modelled.
    """

    def run_callback(self, func, *args):
        # Fire-and-forget on the next scheduler turn.
        filament.spawn_n(func, *args)
        return None

    def io(self, fd, events, ref=True, priority=None):
        """Create (but do not start) a readiness watcher for ``fd``."""
        return _IOWatcher(fd, events, ref=ref, priority=priority)


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
