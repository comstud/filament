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
from _filament.timer import Timer as _Timer

# gevent re-uses greenlet.GreenletExit; filament exposes the same object.
GreenletExit = filament.GreenletExit

# Sentinel: no outcome recorded yet (distinguishes a stored ``None`` value).
_UNSET = object()

# Serial number behind Greenlet.minimal_ident / the default Greenlet.name.
_ident_counter = 0


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
        if run is not None and not callable(run):
            raise TypeError("The run argument must be callable, not %r" % (run,))
        self._run = run
        self._args = args
        self._kwargs = kwargs
        self._filament = None       # underlying filament greenthread once started
        self._start_handle = None   # spawn_later handle if start_later() used
        self._value = _UNSET
        self._exc_info = None       # (type, value, tb) if it raised
        self._finished = False
        self._started = False
        self._start_cancelled = False   # killed before it ever ran
        self._done = filament.Event()   # set exactly once, when finished
        self._links = []
        # Internal, synchronous; see _add_done_callback.  Not _links.
        self._done_callbacks = []
        self._name = None
        self._minimal_ident = None

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
            self._done.set()
            self._fire_done_callbacks()
            self._fire_links()

    def run(self, *args, **kwargs):  # pragma: no cover - subclass hook
        """Override point for Greenlet subclasses that define behaviour."""
        raise NotImplementedError("Greenlet subclass must define run()/_run")

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        """Schedule the greenlet to run (gevent.Greenlet.start)."""
        if self._started or self._start_cancelled:
            # gevent: a greenlet killed before starting can never be started.
            return
        self._started = True
        self._filament = filament.spawn(self._target)

    def start_later(self, seconds):
        """
        Start the greenlet after ``seconds`` (gevent.Greenlet.start_later).

        Implemented via filament.spawn_later; ``kill()`` before the delay
        elapses cancels the pending start.  Faithful mapping.
        """
        if self._started or self._start_cancelled:
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
        timeout it simply returns.  Like gevent, joining a not-yet-started
        greenlet waits (bounded by ``timeout``) rather than returning at once.
        """
        if self._finished:
            return
        self._done.wait(timeout)

    # filament's ``iwait``/``wait`` drive anything exposing ``.wait()``; gevent
    # has no public Greenlet.wait, so this is a harmless superset that makes
    # ``gevent.wait([greenlet])`` genuinely block until completion.
    def wait(self, timeout=None):
        self.join(timeout)

    def get(self, block=True, timeout=None):
        """
        Return the greenlet's value, or re-raise its exception (gevent.get).

        With ``block=False`` and no result yet, raises a bare Timeout exactly
        like gevent; with a ``timeout``, raises Timeout(timeout) on expiry.
        """
        if not self._finished:
            if not block:
                raise filament.Timeout(exception=None)
            if not self._done.wait(timeout):
                raise filament.Timeout(timeout)
        if self._exc_info is not None:
            _reraise(*self._exc_info)
        return self.value

    def _record_kill_outcome(self, exception):
        # gevent __handle_death_before_start semantics: GreenletExit (class or
        # instance) counts as a *successful* value; anything else is a failure
        # recorded as the greenlet's exception.
        if isinstance(exception, type):
            exception = exception()
        if isinstance(exception, GreenletExit):
            self._value = exception
        else:
            self._exc_info = (type(exception), exception, None)
        self._start_cancelled = True
        self._finished = True
        self._done.set()
        self._fire_done_callbacks()
        self._fire_links()

    def kill(self, exception=GreenletExit, block=True, timeout=None):
        """Kill the greenlet (gevent.Greenlet.kill)."""
        if self._finished:
            return
        if self._start_handle is not None and self._filament is None:
            # Started via start_later but not yet running: cancel the pending
            # start and record the outcome.
            self._start_handle.cancel()
            self._record_kill_outcome(exception)
            return
        if not self._started:
            # Never started: mark dead now and refuse any future start().
            self._record_kill_outcome(exception)
            return
        if self._filament is not None:
            if block:
                filament.kill(self._filament, exception)
                self.join(timeout)
            else:
                # Asynchronous kill: schedule the throw without yielding, so
                # (like gevent) the target is not dead yet when we return.
                if not self._filament.dead:
                    _Timer(0, self._filament.throw, exception)

    # -- state ---------------------------------------------------------------

    def ready(self):
        """True once the greenlet has finished (gevent.Greenlet.ready)."""
        return self._finished

    def successful(self):
        """True if finished AND did not raise (gevent.Greenlet.successful)."""
        return self._finished and self._exc_info is None

    @property
    def dead(self):
        """
        True once the greenlet has finished or its start was cancelled.

        gevent parity: a freshly created, never-started greenlet is NOT dead
        (it can still be started); one killed before starting is.
        """
        return self._start_cancelled or (self._started and self._finished)

    def __bool__(self):
        # gevent: True from start() until the greenlet finishes/dies.
        return self._started and not self._finished and \
            not self._start_cancelled

    __nonzero__ = __bool__  # Py2

    @property
    def value(self):
        """The return value (None until finished / if it raised)."""
        return None if self._value is _UNSET else self._value

    @property
    def exception(self):
        """The exception instance the greenlet raised, or None."""
        return self._exc_info[1] if self._exc_info else None

    @property
    def exc_info(self):
        """
        ``(type, value, traceback)`` if the greenlet raised, else a triple of
        ``None`` -- gevent's shape, which callers index into unconditionally
        (exception handlers do ``exc_info[0] is SomeError`` without checking).
        """
        return self._exc_info if self._exc_info else (None, None, None)

    @property
    def args(self):
        """
        The positional arguments the greenlet was spawned with.

        gevent exposes these, and code in the wild reaches through them to
        get at the object a greenlet is running for, via ``greenlet.args[0]``.
        """
        return self._args

    @property
    def kwargs(self):
        """The keyword arguments the greenlet was spawned with."""
        return self._kwargs

    @property
    def minimal_ident(self):
        """
        A small per-process serial number, gevent-style.

        gevent hands these out lazily from a per-hub registry so greenlets get
        short, readable identifiers; we do the same with a counter.
        """
        if self._minimal_ident is None:
            global _ident_counter
            _ident_counter += 1
            self._minimal_ident = _ident_counter
        return self._minimal_ident

    @property
    def name(self):
        """
        Human-readable name, defaulting to ``Greenlet-<minimal_ident>``.

        Settable, as in gevent, and read by logging in real projects.
        """
        if self._name is None:
            self._name = "Greenlet-%d" % self.minimal_ident
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

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

    # -- internal completion callbacks ---------------------------------------
    #
    # Deliberately NOT the same thing as link().  link() is the public gevent
    # API and runs each callback in its own greenthread, so a slow or raising
    # user callback cannot disturb this greenlet.  These run *synchronously*
    # in the finishing greenthread and are for filament's own plumbing only,
    # where the callback is something trivial like a queue put and spawning a
    # greenthread to perform it is exactly the cost we are trying to avoid.
    #
    # This is what lets iwait() observe completions without parking a watcher
    # greenthread on every object: for a 20-way fan-out that is the difference
    # between 20 greenthreads and 40.

    def _add_done_callback(self, callback):
        """Call ``callback(self)`` the instant this greenlet finishes."""
        if self._finished:
            callback(self)
            return
        self._done_callbacks.append(callback)

    def _remove_done_callback(self, callback):
        """Detach a callback added by _add_done_callback, if it is present."""
        try:
            self._done_callbacks.remove(callback)
        except ValueError:
            pass

    def _fire_done_callbacks(self):
        if not self._done_callbacks:
            return
        callbacks = self._done_callbacks
        self._done_callbacks = []
        for cb in callbacks:
            cb(self)

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
    gevent.spawn_raw: cheap spawn returning a raw greenlet (no links/values).

    Returns the underlying filament greenthread so callers can pass it to
    ``gevent.kill``/``killall`` like gevent's raw greenlets.
    """
    if not callable(function):
        raise TypeError("function must be callable")
    return filament.spawn(function, *args, **kwargs)


def kill(greenlet_, exception=GreenletExit):
    """
    gevent.kill: *asynchronously* kill a greenlet.

    gevent's module-level kill schedules the throw and returns without
    blocking (unlike Greenlet.kill's block=True default).
    """
    if isinstance(greenlet_, Greenlet):
        return greenlet_.kill(exception, block=False)
    if not greenlet_.dead:
        _Timer(0, greenlet_.throw, exception)


def killall(greenlets, exception=GreenletExit, block=True, timeout=None):
    """
    gevent.killall: kill a collection of greenlets.

    With ``block=True`` waits for them to die; if ``timeout`` expires first,
    raises Timeout (gevent contract).
    """
    greenlets = list(greenlets)
    for g in greenlets:
        kill(g, exception)
    if block:
        joinall(greenlets, timeout=timeout)
        if not all(_is_done(g) for g in greenlets):
            raise filament.Timeout(timeout)
    else:
        # One yield so the scheduler picks up the queued throws.
        filament.sleep(0)


def _is_done(obj):
    ready = getattr(obj, "ready", None)
    if ready is not None:
        return ready()
    return bool(getattr(obj, "dead", False))


def iwait(objects, timeout=None, count=None):
    """
    gevent.iwait: yield each waitable AS IT COMPLETES (completion order),
    stopping after ``count`` results or when ``timeout`` expires.
    """
    objects = list(objects)
    if count is None:
        count = len(objects)
    count = min(count, len(objects))

    done_q = filament.Queue()
    put = done_q.put

    def _watch(obj):
        waiter = getattr(obj, "join", None) or getattr(obj, "wait", None)
        if waiter is not None:
            try:
                waiter()
            except BaseException:
                # BaseException: a killed greenlet's wait() re-raises
                # GreenletExit, and that still counts as "completed".  The
                # value/exception stays on the object for the caller to fetch.
                pass
        put(obj)

    # Anything that can tell us when it finishes gets a callback; only foreign
    # waitables (raw filaments from spawn_raw, arbitrary objects with a
    # .wait()) need a greenthread parked on them.  That matters: this is the
    # hot path under joinall(), and parking a watcher on every object doubled
    # the greenthread count of every fan-out -- worth ~2x throughput on a
    # 20-way scatter-gather.
    watchers = []
    notified = []
    for obj in objects:
        add_callback = getattr(obj, "_add_done_callback", None)
        if add_callback is None:
            watchers.append(filament.spawn(_watch, obj))
        else:
            notified.append(obj)
            add_callback(put)       # may fire immediately if already finished

    timer = filament.Timeout(timeout, False)
    timer.start()
    try:
        yielded = 0
        while yielded < count:
            try:
                obj = done_q.get()
            except BaseException as e:
                if e is timer:
                    return          # overall budget exhausted -> stop yielding
                raise
            yield obj
            yielded += 1
    finally:
        timer.cancel()
        # Detach from anything still running: the generator can be abandoned
        # (a `count` short of len(objects), a timeout, or the caller simply not
        # exhausting it), and a stale callback would keep this queue alive for
        # as long as the greenlet runs.
        for obj in notified:
            obj._remove_done_callback(put)
        leftovers = [w for w in watchers if not w.dead]
        if leftovers:
            filament.killall(leftovers, block=False)


def wait(objects=None, timeout=None, count=None):
    """
    gevent.wait: wait for waitables; returns those that completed in time.

    ``objects=None`` (gevent: wait for the event loop to drain) returns []
    immediately -- filament has no global loop to drain (documented stub).
    """
    if objects is None:
        return []
    return list(iwait(objects, timeout=timeout, count=count))


def joinall(greenlets, timeout=None, raise_error=False, count=None):
    """
    gevent.joinall: wait for greenlets to finish; returns the FINISHED subset.

    With ``raise_error=True`` re-raises the first failure encountered (in
    completion order), like gevent.
    """
    if not raise_error:
        return wait(greenlets, timeout=timeout, count=count)

    done = []
    for obj in iwait(greenlets, timeout=timeout, count=count):
        if getattr(obj, "exception", None) is not None:
            raise obj.exception
        done.append(obj)
    return done
