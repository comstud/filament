# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.eventlet_compat.greenthread
====================================

Drop-in replacement for ``eventlet.greenthread``, backed entirely by
filament's native greenthread API.

This module is injected into ``sys.modules['eventlet.greenthread']`` by
:func:`filament.eventlet_compat.install`.  Everything here is a *faithful
mapping* onto filament unless a comment explicitly flags a stub.

Terminology note: eventlet's ``GreenThread`` is the object returned by
``eventlet.spawn`` -- you ``.wait()`` on it for the result, ``.kill()`` it, or
``.link()`` a callback.  filament's native ``spawn`` returns a
``_filament.core.Filament`` which already has ``.wait()``/``.dead``, so our
:class:`GreenThread` is a thin wrapper that adds eventlet-shaped ``.kill()`` and
``.link()`` on top and records the outcome (value/exception) so ``.link``
callbacks and ``.wait`` behave like eventlet's.
"""

from __future__ import absolute_import

import filament
from filament import greenthread as _fil_greenthread

# GreenletExit is eventlet's "polite stop" exception, same object filament uses.
GreenletExit = _fil_greenthread.GreenletExit


def getcurrent():
    """Return the greenlet (possibly a GreenThread/Filament) running now."""
    return filament.getcurrent()


def sleep(seconds=0):
    """Cooperatively sleep; ``sleep(0)`` just yields to the scheduler."""
    return filament.sleep(seconds)


class GreenThread(object):
    """
    eventlet-shaped handle for a spawned greenthread.

    We wrap the target callable so the eventual value or exception is captured
    into an internal one-shot future (:class:`filament.AsyncResult`).  That lets
    us support eventlet's ``.wait()`` (re-raises the exception), ``.link()``
    (callback receives *this* GreenThread) and ``.kill()`` while still running
    on filament's native scheduler.
    """

    def __init__(self, run, args, kwargs):
        # One-shot future holding the final value or exception.
        self._result = filament.AsyncResult()
        self._run = run
        self._args = args
        self._kwargs = kwargs
        # Links are (callback, args, kwargs) fired with this GreenThread once
        # it finishes -- exactly eventlet's link contract.
        self._links = []
        # The underlying filament greenthread actually doing the work.
        self._filament = filament.spawn(self._runner)

    def _runner(self):
        # Runs inside the spawned filament greenthread.  We never let an
        # exception escape here: instead we stash it on the future so waiters
        # (and links) observe it the eventlet way.
        try:
            value = self._run(*self._args, **self._kwargs)
        except GreenletExit as exit_exc:
            # eventlet treats a GreenletExit as the greenthread's *result*
            # value (a killed thread's wait() returns the GreenletExit).
            self._result.set(exit_exc)
        except BaseException as err:  # noqa: B902 - faithful capture-all
            self._result.set_exception(err)
        else:
            self._result.set(value)
        finally:
            self._fire_links()

    # -- eventlet API --------------------------------------------------------

    def wait(self):
        """Block until finished; return the value or re-raise the exception."""
        return self._result.get()

    def link(self, func, *args, **kwargs):
        """
        Register ``func(self, *args, **kwargs)`` to run when this finishes.

        If already finished, it is scheduled immediately.  Callbacks run in
        their own fire-and-forget greenthread so a slow/raising callback cannot
        disturb the greenthread that produced the result.
        """
        self._links.append((func, args, kwargs))
        if self._result.ready():
            self._fire_links()
        return self

    def unlink(self, func):
        """Remove a previously linked callback (best-effort, by identity)."""
        self._links = [entry for entry in self._links if entry[0] is not func]

    def _fire_links(self):
        if not self._links:
            return
        links = self._links
        self._links = []
        for func, args, kwargs in links:
            # Pass this GreenThread as the first arg, per eventlet.
            filament.spawn_n(func, self, *args, **kwargs)

    def kill(self, *throw_args):
        """Kill the underlying greenthread (see :func:`kill`)."""
        return kill(self, *throw_args)

    def cancel(self, *throw_args):
        """
        eventlet's ``cancel`` == kill only if it has not started running yet.

        filament greenthreads begin at the next scheduler switch and we can't
        cheaply distinguish "not yet started"; we therefore treat cancel as a
        kill (safe: killing an already-finished greenthread is a no-op).  This
        is a documented minor divergence from eventlet's start-guard.
        """
        return kill(self, *throw_args)

    @property
    def dead(self):
        """True once the greenthread has finished."""
        return self._result.ready()

    def __getattr__(self, name):
        # Forward anything we don't implement to the raw filament greenthread.
        return getattr(self._filament, name)


def spawn(func, *args, **kwargs):
    """Spawn ``func`` and return a :class:`GreenThread` (eventlet.spawn)."""
    return GreenThread(func, args, kwargs)


def spawn_n(func, *args, **kwargs):
    """
    True fire-and-forget spawn (eventlet.spawn_n): schedules ``func`` and
    returns ``None``.  The result/exception are not retrievable.  This is NOT
    an alias for spawn -- it maps to filament's genuine ``spawn_n``.
    """
    return filament.spawn_n(func, *args, **kwargs)


def spawn_after(seconds, func, *args, **kwargs):
    """
    Spawn ``func`` after ``seconds`` (eventlet.spawn_after).

    Backed by filament's ``spawn_later``.  Returns the handle it produces, which
    exposes ``.cancel()`` (cancel before it starts) and ``.wait()``.
    """
    return filament.spawn_later(seconds, func, *args, **kwargs)


def spawn_after_local(seconds, func, *args, **kwargs):
    """
    eventlet.spawn_after_local: same as :func:`spawn_after` here.

    eventlet's *local* variant is auto-cancelled if the spawning greenthread
    dies first; filament has no per-greenthread timer ownership hook, so we map
    it to the plain delayed spawn.  Documented divergence.
    """
    return filament.spawn_later(seconds, func, *args, **kwargs)


def kill(gt, *throw_args):
    """
    Kill greenthread ``gt`` (eventlet.greenthread.kill).

    Accepts either our :class:`GreenThread` wrapper or a raw filament greenlet.
    ``throw_args`` is ``(exception[, value[, tb]])``; default is GreenletExit.
    """
    target = getattr(gt, "_filament", gt)
    if not throw_args:
        return filament.kill(target)
    return filament.kill(target, *throw_args)


def cancel(gt, *throw_args):
    """
    eventlet.greenthread.cancel -- see :meth:`GreenThread.cancel`; treated as a
    kill here (documented divergence from eventlet's not-yet-started guard).
    """
    return kill(gt, *throw_args)
