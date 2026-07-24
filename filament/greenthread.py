# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.greenthread
====================

Greenthread helpers: spawning, killing, and waiting.  These are the
filament-native equivalents of gevent's ``gevent.spawn`` /
``gevent.killall`` and eventlet's ``eventlet.spawn`` / ``spawn_n`` family.

Terminology: a "greenthread" here is a ``_filament.core.Filament`` -- a
``greenlet`` subclass that runs a function under the cooperative scheduler.
``spawn`` returns one; you ``.wait()`` on it to get its result (or to re-raise
whatever it raised).
"""

from __future__ import absolute_import

import sys
import traceback

import greenlet

from _filament.core import spawn as _core_spawn
from _filament.core import sleep as _sleep
from _filament.timer import Timer

# GreenletExit is the "polite" way to stop a greenlet: raised inside it, it
# unwinds the stack but is treated as a normal (non-error) exit by wait().
GreenletExit = greenlet.GreenletExit


def getcurrent():
    """Return the greenlet (possibly a Filament) currently running."""
    return greenlet.getcurrent()


def sleep(seconds=0):
    """
    Cooperatively sleep for ``seconds``.  ``sleep(0)`` just yields to the
    scheduler, giving other greenthreads a chance to run.
    """
    return _sleep(seconds)


def spawn(fn, *args, **kwargs):
    """
    Spawn ``fn(*args, **kwargs)`` as a new greenthread and return the Filament.

    The greenthread does not start running until the scheduler next gets
    control (e.g. the caller sleeps or waits).  Use the returned object's
    ``.wait()`` to retrieve the result or ``.kill()`` via :func:`kill`.
    """
    return _core_spawn(fn, *args, **kwargs)


def _fire_and_forget(fn, args, kwargs):
    # Runs the target and *discards* its result.  Any exception is printed (so
    # bugs aren't silently lost) but never propagated -- there is, by design, no
    # one waiting to receive it.  This mirrors eventlet's spawn_n semantics.
    try:
        fn(*args, **kwargs)
    except GreenletExit:
        pass
    except Exception:
        traceback.print_exc()


def spawn_n(fn, *args, **kwargs):
    """
    Fire-and-forget spawn.  Schedules ``fn`` to run and returns ``None``.

    Unlike :func:`spawn`, the result and any exception are NOT retrievable --
    this is deliberately not just an alias for spawn.  It is slightly cheaper
    conceptually (no one holds the Filament to inspect it) and matches
    eventlet's ``spawn_n``.  Exceptions are printed rather than swallowed
    silently, so genuine errors still surface in logs.
    """
    _core_spawn(_fire_and_forget, fn, args, kwargs)
    return None


class GreenThread(object):
    """
    Thin, optional wrapper giving a spawned Filament an eventlet-ish name.

    We mostly hand back raw Filaments (they already have ``.wait()``/``.kill``
    semantics via this module), so this exists only for API parity / naming.
    """

    def __init__(self, filament):
        self._filament = filament

    def wait(self):
        return self._filament.wait()

    def kill(self, *args, **kwargs):
        return kill(self._filament, *args, **kwargs)

    @property
    def dead(self):
        return self._filament.dead

    def __getattr__(self, name):
        return getattr(self._filament, name)


class _SpawnLaterHandle(object):
    """
    Handle returned by :func:`spawn_later` / :func:`spawn_after`.

    Wraps the scheduler Timer that will spawn the function, plus a one-shot
    result future (a ``Message``) so callers can both ``.cancel()`` before the
    function starts and ``.wait()`` for its eventual result.
    """

    def __init__(self, seconds, fn, args, kwargs):
        # Imported lazily to keep the module import graph shallow.
        from _filament.core import Message
        self._result = Message()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._started = False
        self._cancelled = False
        # A Timer fires on the scheduler of *this* thread after ``seconds`` and
        # spawns the function then.  We chose a Timer over a dedicated sleeping
        # helper greenlet because: (a) it consumes no greenlet while waiting,
        # and (b) cancellation is an O(1) flag flip on the Timer rather than
        # having to throw GreenletExit into a parked greenlet.
        self._timer = Timer(seconds, self._fire)

    def _fire(self):
        # Runs on the scheduler thread when the delay elapses.
        if self._cancelled:
            return
        self._started = True

        def runner(fn=self._fn, args=self._args, kwargs=self._kwargs,
                   result=self._result):
            try:
                result.send(fn(*args, **kwargs))
            except BaseException:
                # Capture the full (type, value, tb) so waiters see a faithful
                # re-raise, GreenletExit included.
                exc_type, exc_value, exc_tb = sys.exc_info()
                result.send_exception(exc_type, exc_value, exc_tb)

        _core_spawn(runner)

    def cancel(self):
        """Cancel the pending call if it has not started running yet."""
        self._cancelled = True
        self._timer.cancel()

    def wait(self, timeout=None):
        """Block until the (eventually spawned) function finishes; return it."""
        return self._result.wait(timeout)


def spawn_later(seconds, fn, *args, **kwargs):
    """
    Schedule ``fn(*args, **kwargs)`` to be spawned after ``seconds``.

    Returns a handle with ``.cancel()`` (cancels if not yet started) and
    ``.wait()`` (blocks for the result).
    """
    return _SpawnLaterHandle(seconds, fn, args, kwargs)


# eventlet spells this "spawn_after"; same thing.
spawn_after = spawn_later


def kill(g, exception=GreenletExit, *args):
    """
    Stop greenthread ``g`` by throwing ``exception`` into it.

    Correctness note: ``throw()`` performs a greenlet switch, so it must run on
    ``g``'s own scheduler/thread.  Calling ``g.throw()`` directly from an
    unrelated greenlet can strand control (the killed greenlet's parent is the
    scheduler, not us, so the switch may not return here).  Instead we schedule
    the throw via a zero-delay ``Timer`` -- that makes the scheduler perform the
    throw cooperatively and hand control back to us normally.  We therefore only
    operate correctly on greenlets belonging to the current thread's scheduler.
    """
    if g.dead:
        return
    current = greenlet.getcurrent()
    if g is current:
        # Killing ourselves: just raise here and now.
        if args:
            raise exception(*args)
        raise exception
    # Schedule the throw on the scheduler; fire immediately (0 delay).
    Timer(0, g.throw, exception, *args)
    # Yield so the scheduler runs the timer (and hence the kill) promptly.
    sleep(0)


def joinall(greenlets, timeout=None, raise_error=False):
    """
    Wait for every greenthread in ``greenlets`` to finish.

    :param timeout: overall wall-clock budget across all of them (``None`` ==
        wait forever).  On expiry we simply return the (possibly still-running)
        list -- we do not raise, matching gevent.
    :param raise_error: if True, re-raise the first exception a greenthread
        raised; otherwise exceptions are ignored (you can still inspect each
        greenthread individually).
    """
    from filament.timeout import Timeout

    greenlets = list(greenlets)
    # ``Timeout(timeout, False)`` == silent expiry: the with-block just exits.
    # ``Timeout(None, ...)`` is an inert no-op, so this also handles timeout=None.
    #
    # The sentinel fires *inside* a blocking ``g.wait()`` below, so we must not
    # let the per-greenthread ``except`` swallow it -- otherwise the timeout only
    # cuts one wait short and joinall keeps blocking for the full remaining set.
    # Re-raise the sentinel instance so it reaches the ``with`` block, which
    # suppresses it, and we return whatever is (still) in ``greenlets``.
    timer = Timeout(timeout, False)
    with timer:
        for g in greenlets:
            try:
                g.wait()
            except GreenletExit:
                # A killed greenthread finishing is not an error to joiners.
                pass
            except BaseException as e:
                if e is timer:
                    # Overall budget exhausted -> stop waiting, return early.
                    raise
                if raise_error:
                    raise
    return greenlets


def killall(greenlets, exception=GreenletExit, block=True, timeout=None):
    """
    Kill every greenthread in ``greenlets``.

    With ``block=True`` (default) we wait for them all to actually finish
    (bounded by ``timeout``) before returning.
    """
    greenlets = list(greenlets)
    for g in greenlets:
        if not g.dead:
            # Schedule each throw cooperatively (see kill()).  We don't sleep
            # between them; a single yield below lets the scheduler drain them.
            Timer(0, g.throw, exception)
    if block:
        joinall(greenlets, timeout=timeout)
    else:
        # Still need one yield so the scheduler picks up the queued throws.
        sleep(0)


def wait(objects=None, timeout=None, count=None):
    """
    Wait for multiple waitables (Filaments, or anything with ``.wait()``).

    :param objects: iterable of waitables.  ``None`` is accepted for gevent
        parity but simply returns immediately (there is no global run loop to
        drain here).
    :param timeout: overall budget; on expiry return whatever finished so far.
    :param count: stop once this many have finished (``None`` == all).

    Returns the list of objects that completed.
    """
    if objects is None:
        return []
    return list(iwait(objects, timeout=timeout, count=count))


def iwait(objects, timeout=None, count=None):
    """
    Iterator form of :func:`wait`: yield each waitable as it completes.

    Simplicity/correctness note: filament's C waitables don't expose a
    select-style "wait on any", so we wait on them in turn.  Because every
    waitable is driven by the same cooperative scheduler, waiting on one still
    lets the others make progress, and a completed one returns immediately --
    so the practical behaviour (all of them get waited on within ``timeout``)
    is correct even though we don't yield in strict completion order.
    """
    from filament.timeout import Timeout

    objects = list(objects)
    yielded = 0
    with Timeout(timeout, False):
        for obj in objects:
            waiter = getattr(obj, "wait", None)
            if waiter is not None:
                try:
                    waiter()
                except Exception:
                    # Completion is what we report; the value/exception is the
                    # caller's to fetch from the object itself.
                    pass
            yield obj
            yielded += 1
            if count is not None and yielded >= count:
                return


def with_timeout(seconds, function, *args, **kwargs):
    """
    Convenience: run ``function`` under a :class:`filament.timeout.Timeout`.

    Delegates to :func:`filament.timeout.with_timeout`; see there for the
    ``timeout_value`` keyword.
    """
    from filament.timeout import with_timeout as _wt
    return _wt(seconds, function, *args, **kwargs)
