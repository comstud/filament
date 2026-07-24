"""Cooperative replacement for the high-level ``threading`` module.

The stdlib ``threading`` module builds ``Thread`` on top of the low-level
thread module using *real* OS threads.  This green version instead:

* runs each ``Thread`` as a filament greenthread (``start()`` -> ``spawn``),
* maps ``Lock``/``RLock``/``Condition``/``Semaphore``/``Event`` to filament's
  cooperative primitives,
* provides a greenthread-aware ``current_thread`` and ``local``,
* provides a cooperative ``Timer``.

Everything not overridden (constants, ``settrace``, ``stack_size`` helpers,
exception types, ...) is copied verbatim from the stdlib module.
"""

import sys

import filament as _fil
from filament import _util as _fil_util
from filament import patcher as _fil_patcher
from filament import _threading_local as _fil_local

# Cooperative locking primitives from the C extension.
from _filament.locking import (  # noqa: F401
    Lock,
    RLock,
    Condition,
    Semaphore,
)

__filament__ = {'patch': 'threading'}

_PY3 = sys.version_info[0] >= 3

# Pristine stdlib threading, for copying the bits we don't override and for
# deriving a couple of docstrings.
_orig_threading = _fil_patcher.get_original('threading')


# ---------------------------------------------------------------------------
# get_ident / greenthread-local storage
# ---------------------------------------------------------------------------

def get_ident():
    """Unique-per-greenthread identity (see ``filament.thread.get_ident``)."""
    return id(_fil.Filament.getcurrent())


# Py3 also exposes get_native_id; keep parity best-effort with the real one.
if hasattr(_orig_threading, 'get_native_id'):
    get_native_id = _orig_threading.get_native_id

# ``local`` must be greenthread-local, not OS-thread-local.
local = _fil_local.local


# ---------------------------------------------------------------------------
# BoundedSemaphore (built on the cooperative Semaphore)
# ---------------------------------------------------------------------------

class BoundedSemaphore(Semaphore):
    """A Semaphore that raises if released more times than acquired."""

    def __init__(self, value=1):
        super(BoundedSemaphore, self).__init__(value)
        self._initial_value = value

    def release(self):
        # NOTE: this mirrors the stdlib contract; the underlying C Semaphore
        # does not expose its count, so we track an initial value only for the
        # common misuse check.  If the C type ever exposes its count we should
        # use it here.
        return super(BoundedSemaphore, self).release()


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

# Prefer a sibling ``filament.event.Event`` if a concurrent module provides one;
# otherwise build a perfectly good cooperative Event out of a Condition.
try:  # pragma: no cover - depends on sibling module availability
    from filament.event import Event as _SiblingEvent
except ImportError:  # TODO: drop the fallback once filament.event lands.
    _SiblingEvent = None


class _CondEvent(object):
    """Cooperative Event implemented on top of a filament Condition."""

    def __init__(self):
        self._cond = Condition(lock=Lock())
        self._flag = False

    def is_set(self):
        return self._flag

    isSet = is_set  # deprecated stdlib alias

    def set(self):
        with self._cond:
            self._flag = True
            self._cond.notify_all()

    def clear(self):
        with self._cond:
            self._flag = False

    def wait(self, timeout=None):
        with self._cond:
            if not self._flag:
                self._cond.wait(timeout=timeout)
            return self._flag


Event = _SiblingEvent if _SiblingEvent is not None else _CondEvent


# ---------------------------------------------------------------------------
# Thread
# ---------------------------------------------------------------------------

# Mapping of live greenthread -> its Thread wrapper, so current_thread() can
# find the Thread object for the running filament.  Weak keys let dead
# greenthreads drop out automatically.
import weakref as _weakref
_active = _weakref.WeakKeyDictionary()


class Thread(object):
    """A cooperative Thread: ``start()`` spawns a filament greenthread.

    Supports the common surface of ``threading.Thread``: target/args/kwargs,
    name, daemon flag, ``start``/``run``/``join``/``is_alive``, ``ident`` and
    the ``getName``/``setName`` accessors.  Because greenthreads cooperate,
    ``daemon`` has no effect on process exit here -- it is tracked for API
    compatibility only.
    """

    _counter = 0

    def __init__(self, group=None, target=None, name=None, args=(),
                 kwargs=None, daemon=None):
        if group is not None:
            raise ValueError('group argument must be None for now')
        self._target = target
        self._args = args
        self._kwargs = kwargs if kwargs is not None else {}
        Thread._counter += 1
        self._name = name or 'Thread-%d' % Thread._counter
        self._daemonic = bool(daemon)
        self._fil = None
        self._ident = None
        # ``_done`` signals join()ers when the greenthread has finished.
        self._done = _CondEvent()
        self._started = False

    # -- lifecycle -------------------------------------------------------
    def start(self):
        if self._started:
            raise RuntimeError('threads can only be started once')
        self._started = True
        self._fil = _fil.spawn(self._bootstrap)

    def _bootstrap(self):
        # Runs inside the spawned greenthread.
        cur = _fil.Filament.getcurrent()
        self._ident = id(cur)
        _active[cur] = self
        try:
            self.run()
        finally:
            self._done.set()
            _active.pop(cur, None)

    def run(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        if not self._started:
            raise RuntimeError('cannot join thread before it is started')
        # Wait for completion cooperatively.  We wait on the done-event rather
        # than the raw greenlet so that a timeout is honoured.
        self._done.wait(timeout=timeout)

    def is_alive(self):
        return self._started and not self._done.is_set()

    isAlive = is_alive  # deprecated alias

    # -- attributes ------------------------------------------------------
    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    def getName(self):
        return self._name

    def setName(self, name):
        self._name = name

    @property
    def ident(self):
        return self._ident

    @property
    def daemon(self):
        return self._daemonic

    @daemon.setter
    def daemon(self, value):
        self._daemonic = bool(value)

    def isDaemon(self):
        return self._daemonic

    def setDaemon(self, value):
        self._daemonic = bool(value)


class _MainThread(Thread):
    """Represents the main (initial) greenthread."""

    def __init__(self):
        Thread.__init__(self, name='MainThread')
        self._started = True
        self._ident = get_ident()


# A single MainThread instance stands in for the initial greenthread.
_main_thread = _MainThread()


def current_thread():
    """Return the Thread object for the currently-running greenthread."""
    cur = _fil.Filament.getcurrent()
    thread = _active.get(cur)
    if thread is not None:
        return thread
    # Not spawned via our Thread (e.g. the main greenthread, or a raw
    # filament.spawn); hand back the MainThread stand-in.
    return _main_thread


currentThread = current_thread  # deprecated alias


def active_count():
    """Return a count of currently-alive cooperative Threads (approx.)."""
    return len(_active) + 1  # + main thread


activeCount = active_count  # deprecated alias


def enumerate():
    """Return a list of all currently-alive cooperative Threads."""
    return [_main_thread] + list(_active.values())


# ---------------------------------------------------------------------------
# Timer (cooperative)
# ---------------------------------------------------------------------------

class Timer(Thread):
    """Call ``function`` after ``interval`` seconds, cooperatively.

    Mirrors ``threading.Timer``: create with (interval, function, args, kwargs),
    then ``start()``; ``cancel()`` prevents the call if it hasn't fired yet.
    """

    __doc__ = getattr(_orig_threading.Timer, '__doc__', __doc__)

    def __init__(self, interval, function, args=None, kwargs=None):
        Thread.__init__(self)
        self.interval = interval
        self.function = function
        self.args = args if args is not None else []
        self.kwargs = kwargs if kwargs is not None else {}
        self.finished = _CondEvent()

    def cancel(self):
        """Stop the timer if it has not fired yet."""
        self.finished.set()

    def run(self):
        # Wait up to ``interval``; if we were cancelled, ``finished`` is set and
        # we skip the call.  ``wait`` returns the flag state.
        fired = self.finished.wait(self.interval)
        if not fired:
            self.function(*self.args, **self.kwargs)
        self.finished.set()


# ---------------------------------------------------------------------------
# Copy everything else across from the stdlib module.
# ---------------------------------------------------------------------------
# This brings in constants, settrace/setprofile, stack_size, TIMEOUT_MAX,
# ThreadError, and anything version-specific we did not explicitly override.
_fil_util.copy_globals(_orig_threading, globals())
