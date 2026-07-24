# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.greenlet
===============================

The gevent ``Greenlet`` class and the module-level spawn/kill/wait helpers,
backed by filament.

gevent's ``Greenlet`` differs from eventlet's GreenThread in one important way:
it supports *deferred start*.  ``gevent.spawn(fn)`` starts immediately, but

    g = Greenlet(fn, *args)
    g.start()

is also valid -- the greenlet is created but does not run until ``start()``.
filament's ``spawn`` always schedules the function, so we implement the deferred
case by holding ``fn``/args and only calling ``filament.spawn`` inside
:meth:`Greenlet.start`.

Outcome tracking: we wrap the target so its value/exception is captured, which
lets us implement ``.value`` / ``.exception`` / ``.successful()`` / ``.get()``
/ ``.ready()`` and gevent-style ``.link()`` faithfully.
"""

from __future__ import absolute_import

import filament

# gevent re-uses greenlet.GreenletExit; filament exposes the same object.
GreenletExit = filament.GreenletExit

# Sentinel: no outcome recorded yet (distinguishes a stored ``None`` value).
_UNSET = object()


class Greenlet(object):
    """
    filament-backed implementation of ``gevent.Greenlet``.

    Faithful mappings: ``spawn`` (classmethod), ``start``, ``join``, ``get``,
    ``kill``, ``ready``, ``successful``, ``dead``, ``value``, ``exception``,
    ``link``.  The one documented divergence is :meth:`start_later` semantics
    (see there).
    """

    def __init__(self, run=None, *args, **kwargs):
        # ``run`` may be None if a subclass overrides ``_run``; gevent supports
        # subclassing Greenlet and defining ``_run``.
        self._run = run
        self._args = args
        self._kwargs = kwargs
        self._filament = None       # underlying filament greenthread once started
        self._start_handle = None   # spawn_later handle if start_later() used
        self._value = _UNSET
        self._exc_info = None       # (type, value, tb) if it raised
        self._finished = False
        self._started = False
        self._links = []

    # -- construction helpers ------------------------------------------------

    @classmethod
    def spawn(cls, *args, **kwargs):
        """Create a Greenlet and start it immediately (gevent.Greenlet.spawn)."""
        g = cls(*args, **kwargs)
        g.start()
        return g

    # -- the body ------------------------------------------------------------

    def _target(self):
        # Runs inside the filament greenthread.  Subclasses may override
        # ``_run``; if ``self._run`` is None we look for that.
        run = self._run
        if run is None:
            run = getattr(self, "_run_impl", None) or self.run
        try:
            value = run(*self._args, **self._kwargs)
        except GreenletExit as exit_exc:
            # gevent stores a GreenletExit as the greenlet's *value*.
            self._value = exit_exc
        except BaseException:  # noqa: B902 - faithful capture-all
            import sys
            self._exc_info = sys.exc_info()
        else:
            self._value = value
        finally:
            self._finished = True
            self._fire_links()

    def run(self, *args, **kwargs):  # pragma: no cover - subclass hook
        """Override point for Greenlet subclasses that define behaviour."""
        raise NotImplementedError("Greenlet subclass must define run()/_run")

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        """Schedule the greenlet to run (gevent.Greenlet.start)."""
        if self._started:
            return
        self._started = True
        self._filament = filament.spawn(self._target)

    def start_later(self, seconds):
        """
        Start the greenlet after ``seconds`` (gevent.Greenlet.start_later).

        Implemented via filament.spawn_later; ``kill()`` before the delay
        elapses cancels the pending start.  Faithful mapping.
        """
        if self._started:
            return
        self._started = True
        self._start_handle = filament.spawn_later(seconds, self._run_deferred)

    def _run_deferred(self):
        # Invoked by the spawn_later timer; runs the body in the current
        # (freshly spawned) greenthread.
        self._filament = filament.getcurrent()
        self._target()

    def join(self, timeout=None):
        """
        Block until the greenlet finishes, or ``timeout`` seconds pass.

        Never raises the greenlet's exception (use :meth:`get` for that); on
        timeout it simply returns.  Faithful to gevent.
        """
        if self._finished or not self._started:
            return
        with filament.Timeout(timeout, False):
            if self._filament is not None:
                # wait() re-raises the body's exception, but our _target never
                # lets one escape, so this just blocks until completion.
                self._filament.wait()
            else:
                # Started via start_later but not yet running: poll cooperatively.
                while not self._finished:
                    filament.sleep(0)

    def get(self, block=True, timeout=None):
        """
        Return the greenlet's value, or re-raise its exception (gevent.get).

        With ``block=False`` and no result yet, raises the timeout/loop error
        gevent raises -- here filament's Timeout.
        """
        if not self._finished:
            if not block:
                raise filament.Timeout("Greenlet is not ready")
            self.join(timeout)
            if not self._finished:
                raise filament.Timeout(timeout)
        if self._exc_info is not None:
            _reraise(*self._exc_info)
        return self._value

    def kill(self, exception=GreenletExit, block=True, timeout=None):
        """Kill the greenlet (gevent.Greenlet.kill)."""
        if self._finished:
            return
        if self._start_handle is not None and self._filament is None:
            # Not yet started (delayed): cancel the pending start instead.
            self._start_handle.cancel()
            self._value = exception if isinstance(exception, BaseException) \
                else exception()
            self._finished = True
            self._fire_links()
            return
        if self._filament is not None:
            filament.kill(self._filament, exception)
            if block:
                self.join(timeout)

    # -- state ---------------------------------------------------------------

    def ready(self):
        """True once the greenlet has finished (gevent.Greenlet.ready)."""
        return self._finished

    def successful(self):
        """True if finished AND did not raise (gevent.Greenlet.successful)."""
        return self._finished and self._exc_info is None

    @property
    def dead(self):
        """True if it never started, or has finished."""
        return (not self._started) or self._finished

    @property
    def value(self):
        """The return value (None until finished / if it raised)."""
        return None if self._value is _UNSET else self._value

    @property
    def exception(self):
        """The exception instance the greenlet raised, or None."""
        return self._exc_info[1] if self._exc_info else None

    # -- links ---------------------------------------------------------------

    def link(self, callback):
        """
        Call ``callback(self)`` when the greenlet finishes (gevent link).

        If already finished, schedule immediately.  Runs in a fire-and-forget
        greenthread so a slow/raising callback can't disturb this greenlet.
        """
        self._links.append(callback)
        if self._finished:
            self._fire_links()
        return self

    # gevent distinguishes link_value / link_exception; we provide them as
    # documented approximations that fire on the matching outcome.
    def link_value(self, callback):
        """Link that fires only if the greenlet succeeded."""
        def _cb(g):
            if g.successful():
                callback(g)
        return self.link(_cb)

    def link_exception(self, callback):
        """Link that fires only if the greenlet raised."""
        def _cb(g):
            if not g.successful():
                callback(g)
        return self.link(_cb)

    def _fire_links(self):
        if not self._links:
            return
        links = self._links
        self._links = []
        for cb in links:
            filament.spawn_n(cb, self)

    def __repr__(self):
        return "<gevent_compat.Greenlet at 0x%x finished=%r>" % (
            id(self), self._finished)


# -- py2/py3 re-raise helper (module-local; mirrors filament.event) ----------
import sys as _sys

if _sys.version_info[0] >= 3:
    def _reraise(exc_type, exc_value, exc_tb):
        if exc_value is None:
            exc_value = exc_type()
        if exc_tb is not None and exc_value.__traceback__ is not exc_tb:
            raise exc_value.with_traceback(exc_tb)
        raise exc_value
else:  # pragma: no cover - Python 2
    exec("def _reraise(exc_type, exc_value, exc_tb):\n"
         "    raise exc_type, exc_value, exc_tb\n")


# ---------------------------------------------------------------------------
# Module-level spawn/kill/wait helpers (gevent top-level API).
# ---------------------------------------------------------------------------
def spawn(function, *args, **kwargs):
    """gevent.spawn: create and start a :class:`Greenlet`."""
    return Greenlet.spawn(function, *args, **kwargs)


def spawn_later(seconds, function, *args, **kwargs):
    """gevent.spawn_later: create a Greenlet that starts after ``seconds``."""
    g = Greenlet(function, *args, **kwargs)
    g.start_later(seconds)
    return g


def spawn_raw(function, *args, **kwargs):
    """
    gevent.spawn_raw: fire-and-forget spawn returning a raw greenlet.

    Maps to filament.spawn_n (no result tracking), matching gevent's "cheap,
    no callbacks" contract.  Returns None (filament's spawn_n has no handle).
    """
    return filament.spawn_n(function, *args, **kwargs)


def kill(greenlet_, exception=GreenletExit, block=True, timeout=None):
    """gevent.kill: kill a Greenlet (or raw filament greenthread)."""
    if isinstance(greenlet_, Greenlet):
        return greenlet_.kill(exception, block=block, timeout=timeout)
    return filament.kill(greenlet_, exception)


def killall(greenlets, exception=GreenletExit, block=True, timeout=None):
    """gevent.killall: kill a collection of greenlets."""
    greenlets = list(greenlets)
    for g in greenlets:
        if isinstance(g, Greenlet):
            g.kill(exception, block=False)
        else:
            filament.kill(g, exception)
    if block:
        joinall(greenlets, timeout=timeout)


def joinall(greenlets, timeout=None, raise_error=False, count=None):
    """gevent.joinall: wait for all greenlets to finish."""
    greenlets = list(greenlets)
    with filament.Timeout(timeout, False):
        for g in greenlets:
            try:
                g.join()
            except Exception:
                if raise_error:
                    raise
    return greenlets


def wait(objects=None, timeout=None, count=None):
    """gevent.wait: wait for waitables; delegates to filament.wait."""
    return filament.wait(objects, timeout=timeout, count=count)


def iwait(objects, timeout=None, count=None):
    """gevent.iwait: iterator form of :func:`wait`."""
    return filament.iwait(objects, timeout=timeout, count=count)
