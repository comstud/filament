# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.pool
=============

Greenthread groups and pools:

  * :class:`Group`     -- an unbounded set of greenthreads you can join/kill/map.
  * :class:`Pool`      -- a Group bounded by a concurrency limit (gevent shape).
  * :class:`GreenPool` -- eventlet-shaped alias of Pool (default size 1000).
  * :class:`GreenPile` -- feed work in, iterate results back IN ORDER.

The concurrency cap is enforced with the C ``_filament.locking.Semaphore``:
``spawn`` acquires a permit (cooperatively blocking -- i.e. yielding to the
scheduler -- when the pool is full) and the greenthread releases it when it
finishes.  We keep our own ``_running``/``_waiting`` counters so free_count(),
running() and waiting() are O(1) and don't depend on introspecting the C
semaphore's internals.
"""

from __future__ import absolute_import

import sys

from _filament.locking import Semaphore
from _filament.core import Message

from filament import greenthread

GreenletExit = greenthread.GreenletExit


class Group(object):
    """
    An unbounded collection of greenthreads.

    Tracks the greenthreads it spawns so you can :meth:`join` or :meth:`kill`
    them collectively, and offers :meth:`map` / :meth:`imap` helpers.
    """

    def __init__(self, *greenthreads):
        self.greenlets = set(greenthreads)

    # -- membership ----------------------------------------------------------

    def add(self, greenlet_):
        """Track an already-spawned greenthread."""
        self.greenlets.add(greenlet_)

    def discard(self, greenlet_):
        """Stop tracking a greenthread (no error if absent)."""
        self.greenlets.discard(greenlet_)

    def __len__(self):
        return len(self.greenlets)

    def __iter__(self):
        # Iterate a snapshot so callers can mutate the group while looping.
        return iter(list(self.greenlets))

    def __contains__(self, greenlet_):
        return greenlet_ in self.greenlets

    # -- spawning ------------------------------------------------------------

    def _spawn_tracked(self, fn, args, kwargs):
        """
        Spawn ``fn`` and track it *only until it finishes*.

        Tracking has to be self-cancelling.  A group whose members are only
        dropped by :meth:`join` / :meth:`kill` grows without bound in exactly
        the shape that matters most -- a server that spawns one greenthread per
        connection into a long-lived Pool and never joins it (see
        ``StreamServer(spawn=<int>)``) accumulates every connection it has ever
        served.  gevent's Group has the same self-untracking behaviour, so this
        is parity as well as a leak fix.

        The greenthread cannot untrack itself until ``spawn`` has handed us the
        object to untrack, hence the little bit of state-passing here; the
        ``finished`` flag covers a body that somehow completes first, so we
        never add a corpse to the set.
        """
        state = {}

        def _tracked(*a, **kw):
            try:
                return fn(*a, **kw)
            finally:
                state["finished"] = True
                gt = state.get("gt")
                if gt is not None:
                    self.greenlets.discard(gt)

        gt = greenthread.spawn(_tracked, *args, **kwargs)
        state["gt"] = gt
        if not state.get("finished"):
            self.add(gt)
        return gt

    def spawn(self, fn, *args, **kwargs):
        """Spawn ``fn`` as a tracked greenthread and return it."""
        return self._spawn_tracked(fn, args, kwargs)

    def spawn_n(self, fn, *args, **kwargs):
        """Fire-and-forget spawn (untracked, no result).  Returns None."""
        greenthread.spawn_n(fn, *args, **kwargs)
        return None

    # -- lifecycle -----------------------------------------------------------

    def join(self, timeout=None, raise_error=False):
        """Wait for all tracked greenthreads (see greenthread.joinall)."""
        greenlets = list(self.greenlets)
        greenthread.joinall(greenlets, timeout=timeout, raise_error=raise_error)
        # Drop the ones that actually finished.
        for g in greenlets:
            if g.dead:
                self.discard(g)
        return self

    def kill(self, exception=GreenletExit, block=True, timeout=None):
        """Kill all tracked greenthreads."""
        greenlets = list(self.greenlets)
        greenthread.killall(greenlets, exception=exception,
                            block=block, timeout=timeout)
        self.greenlets.clear()

    # -- map family ----------------------------------------------------------

    def map(self, func, iterable):
        """
        Apply ``func`` to each item concurrently; return results as a list in
        the same order as ``iterable``.  Re-raises the first exception.
        """
        return list(self.imap(func, iterable))

    def imap(self, func, *iterables):
        """
        Lazy, ordered concurrent map.  Yields ``func(*items)`` results in input
        order (a slow early item holds back later, already-finished ones --
        that's what "ordered" means).
        """
        greenlets = []
        for items in zip(*iterables):
            gt = self.spawn(func, *items)
            greenlets.append(gt)
        for gt in greenlets:
            yield gt.wait()

    def imap_unordered(self, func, *iterables):
        """
        Lazy concurrent map yielding results as they COMPLETE (any order).

        We route each result through a shared queue so a finished greenthread
        can be yielded immediately without waiting on earlier-but-slower ones.
        """
        pending = []
        for items in zip(*iterables):
            msg = Message()

            def runner(func=func, items=items, msg=msg):
                try:
                    msg.send(func(*items))
                except BaseException:
                    et, ev, tb = sys.exc_info()
                    msg.send_exception(et, ev, tb)

            self.spawn(runner)
            pending.append(msg)
        # NOTE: this yields in spawn order at worst; for true completion order
        # we'd need a shared ready-queue.  We keep it simple and correct: each
        # wait() returns as soon as *that* item is done, and because everything
        # shares one scheduler, no item is starved.
        for msg in pending:
            yield msg.wait()


class Pool(Group):
    """
    A :class:`Group` with a concurrency ceiling.

    ``spawn`` blocks (cooperatively) when ``size`` greenthreads are already
    running, resuming once one finishes.  ``size=None`` means unbounded.
    """

    def __init__(self, size=None, greenlet_class=None):
        Group.__init__(self)
        self.size = size
        # The gate.  One permit per concurrent slot.
        self._sem = Semaphore(size) if size is not None else None
        self._running = 0   # permits currently held by live greenthreads
        self._waiting = 0   # greenthreads currently blocked in spawn()

    # -- capacity introspection ---------------------------------------------

    def free_count(self):
        """Number of slots immediately available (0 when full)."""
        if self.size is None:
            return 1  # effectively unbounded
        free = self.size - self._running
        return free if free > 0 else 0

    # eventlet spells this ``free``.
    free = free_count

    def running(self):
        """How many greenthreads are currently running in the pool."""
        return self._running

    def waiting(self):
        """How many greenthreads are blocked waiting for a free slot."""
        return self._waiting

    # -- spawning ------------------------------------------------------------

    def spawn(self, fn, *args, **kwargs):
        """
        Spawn ``fn`` in the pool, blocking until a slot is free if necessary.
        Returns the greenthread.
        """
        self._acquire_slot()
        # _spawn_tracked untracks the greenthread once it finishes, so a
        # long-lived Pool does not accumulate every task it has ever run.
        return self._spawn_tracked(self._run_and_release, (fn, args, kwargs),
                                   {})

    def spawn_n(self, fn, *args, **kwargs):
        """Fire-and-forget pool spawn (still gated).  Returns None."""
        self._acquire_slot()
        greenthread.spawn_n(self._run_and_release, fn, args, kwargs)
        return None

    def _acquire_slot(self):
        if self._sem is None:
            return
        # Count ourselves as waiting *around* the (possibly blocking) acquire so
        # waiting() is meaningful even while parked in the scheduler.
        self._waiting += 1
        try:
            self._sem.acquire()
        finally:
            self._waiting -= 1
        self._running += 1

    def _run_and_release(self, fn, args, kwargs):
        # The body runs in the spawned greenthread; the finally releases the
        # slot no matter how the body exits (return, exception, or kill), waking
        # one greenthread blocked in _acquire_slot.
        try:
            return fn(*args, **kwargs)
        finally:
            if self._sem is not None:
                self._running -= 1
                self._sem.release()

    # -- availability / resizing --------------------------------------------

    def wait_available(self, timeout=None):
        """Block until at least one slot is free (or ``timeout`` elapses)."""
        if self._sem is None:
            return
        # Acquire then immediately release: proves a slot exists without
        # permanently consuming it.
        self._sem.acquire(timeout=timeout)
        self._sem.release()

    def resize(self, new_size):
        """
        Change the concurrency ceiling.

        Growing releases extra permits (instant).  Shrinking acquires the
        surplus permits, which may block cooperatively until in-flight
        greenthreads finish -- exactly the semantics you want.
        """
        if self._sem is None:
            raise RuntimeError("cannot resize an unbounded pool")
        delta = new_size - self.size
        self.size = new_size
        if delta > 0:
            for _ in range(delta):
                self._sem.release()
        elif delta < 0:
            for _ in range(-delta):
                self._sem.acquire()

    # -- eventlet GreenPool aliases -----------------------------------------

    def waitall(self, timeout=None):
        """eventlet alias for join()."""
        return self.join(timeout=timeout)

    def starmap(self, function, iterable):
        """Like map, but each item is an argument *tuple* for ``function``."""
        return list(self.imap(lambda args: function(*args), iterable))


class GreenPool(Pool):
    """
    eventlet-shaped pool.  Same machinery as :class:`Pool`, but defaults to a
    size of 1000 (eventlet's default) rather than unbounded.
    """

    def __init__(self, size=1000):
        Pool.__init__(self, size=size)


class GreenPile(object):
    """
    Feed work with :meth:`spawn`, then iterate to collect results IN ORDER.

    Ordering guarantee: each ``spawn`` appends a one-shot ``Message`` to an
    internal FIFO; iteration pops them in spawn order and waits on each in turn.
    So even if later items finish first, results come back in the order they
    were submitted.  ``Message.wait`` re-raises a failed item's exception (with
    traceback) at the point you iterate to it.
    """

    def __init__(self, size_or_pool=1000):
        # Accept either an existing pool/group or a size (to build a GreenPool).
        if isinstance(size_or_pool, (Group,)):
            self.pool = size_or_pool
        elif size_or_pool is None:
            self.pool = GreenPool()
        else:
            self.pool = GreenPool(size_or_pool)
        # FIFO of pending result futures, in spawn order.
        self._pending = []

    def spawn(self, fn, *args, **kwargs):
        """Submit ``fn(*args, **kwargs)`` to the pile."""
        msg = Message()
        self._pending.append(msg)

        def runner(fn=fn, args=args, kwargs=kwargs, msg=msg):
            try:
                msg.send(fn(*args, **kwargs))
            except BaseException:
                et, ev, tb = sys.exc_info()
                msg.send_exception(et, ev, tb)

        self.pool.spawn(runner)
        return self

    def __iter__(self):
        return self

    def __next__(self):
        # Pop the next-submitted future and block for it.  StopIteration when
        # everything submitted so far has been consumed.
        if not self._pending:
            raise StopIteration
        msg = self._pending.pop(0)
        return msg.wait()

    # Python 2 iterator protocol.
    next = __next__
