# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.pool
===========================

Drop-in replacement for ``gevent.pool`` (injected as
``sys.modules['gevent.pool']``).

Unlike the eventlet shim (which maps onto :mod:`filament.pool` directly), this
is a real port of gevent's ``Group``/``Pool`` semantics onto the compat
:class:`~filament.gevent_compat.greenlet.Greenlet`:

  * ``spawn`` returns a gevent-shaped Greenlet (get/link/kill all work).
  * ``add``/``discard`` manage pool slots; ``add`` on a full pool raises
    :class:`PoolFull` when non-blocking.
  * finished greenlets are auto-discarded (``len(pool)`` reflects live work).
  * ``imap``/``imap_unordered`` start work eagerly and honour ``maxsize``;
    ``imap_unordered`` yields in COMPLETION order.
  * ``join`` returns True/False (emptied vs. timed out).
"""

from __future__ import absolute_import

import filament

from filament.gevent_compat import greenlet as _greenlet

# ``Empty``/``Full`` live in ``queue`` (Py3) or ``Queue`` (Py2).
try:
    import queue as _queue
except ImportError:  # pragma: no cover - Python 2
    import Queue as _queue

Greenlet = _greenlet.Greenlet
GreenletExit = _greenlet.GreenletExit


class PoolFull(_queue.Full):
    """Raised by ``Pool.add`` when the pool is full (gevent parity)."""


class Group(object):
    """A growable set of greenlets with collective join/kill/map (gevent)."""

    greenlet_class = Greenlet

    def __init__(self, *args):
        if len(args) > 1:
            raise TypeError("Group() takes at most one iterable argument")
        self.greenlets = set(args[0]) if args else set()
        for g in list(self.greenlets):
            self._autodiscard(g)

    def __repr__(self):
        return "<%s at 0x%x %r>" % (
            type(self).__name__, id(self), list(self.greenlets))

    def __len__(self):
        return len(self.greenlets)

    def __contains__(self, item):
        return item in self.greenlets

    def __iter__(self):
        # Iterate a snapshot so callers can mutate the group while looping.
        return iter(list(self.greenlets))

    # -- membership ----------------------------------------------------------

    def _autodiscard(self, greenlet_):
        # Auto-discard on completion, like gevent's rawlink(self._discard).
        link = getattr(greenlet_, "link", None)
        if link is not None:
            link(self.discard)

    def add(self, greenlet_):
        """Begin tracking ``greenlet_``; it is discarded when it finishes."""
        if greenlet_ in self.greenlets:
            return
        self.greenlets.add(greenlet_)
        self._autodiscard(greenlet_)

    def discard(self, greenlet_):
        """Stop tracking ``greenlet_`` (no error if absent)."""
        if greenlet_ in self.greenlets:
            self.greenlets.remove(greenlet_)
            self._discarded(greenlet_)

    def _discarded(self, greenlet_):
        # Hook for Pool to release the slot exactly once per tracked greenlet.
        pass

    # -- spawning ------------------------------------------------------------

    def start(self, greenlet_):
        """Add ``greenlet_`` and start it (gevent.Group.start)."""
        self.add(greenlet_)
        greenlet_.start()

    def spawn(self, function, *args, **kwargs):
        """Spawn a tracked Greenlet running ``function`` and return it."""
        g = self.greenlet_class(function, *args, **kwargs)
        self.start(g)
        return g

    # -- lifecycle -----------------------------------------------------------

    def join(self, timeout=None, raise_error=False):
        """
        Wait for this group to become empty *at least once*.

        Returns True if it became empty, False on timeout (gevent parity).
        With ``raise_error=True``, re-raises the first failure encountered.
        """
        import time as _time
        deadline = None if timeout is None else _time.time() + timeout
        while True:
            current = list(self.greenlets)
            if not current:
                return True
            if deadline is None:
                remaining = None
            else:
                remaining = deadline - _time.time()
                if remaining <= 0:
                    return False
            _greenlet.joinall(current, timeout=remaining,
                              raise_error=raise_error)
            # Let the auto-discard links fire before re-checking emptiness.
            filament.sleep(0)
            if all(_ready(g) for g in list(self.greenlets)):
                for g in list(self.greenlets):
                    if _ready(g):
                        self.discard(g)

    def kill(self, exception=GreenletExit, block=True, timeout=None):
        """Kill every tracked greenlet."""
        current = list(self.greenlets)
        _greenlet.killall(current, exception=exception, block=block,
                          timeout=timeout)
        if block:
            for g in current:
                self.discard(g)

    def killone(self, greenlet_, exception=GreenletExit, block=True,
                timeout=None):
        """Kill one tracked greenlet (no-op if it is not tracked)."""
        if greenlet_ in self.greenlets:
            greenlet_.kill(exception, block=block, timeout=timeout)
            self.discard(greenlet_)

    # -- map family ----------------------------------------------------------

    def map(self, func, iterable):
        """Concurrent ordered map; re-raises the first exception."""
        return list(self.imap(func, iterable))

    def imap(self, func, *iterables, **kwargs):
        """
        Eager, ordered concurrent map (gevent IMap).

        Work starts immediately (not on first ``next()``); results are yielded
        in submit order.  ``maxsize`` bounds how many unconsumed results may
        accumulate before workers block.
        """
        maxsize = kwargs.pop("maxsize", None)
        if kwargs:
            raise TypeError("unexpected keyword arguments: %r" % (kwargs,))
        return _IMap(self, func, iterables, maxsize, ordered=True)

    def imap_unordered(self, func, *iterables, **kwargs):
        """Eager concurrent map yielding results in COMPLETION order."""
        maxsize = kwargs.pop("maxsize", None)
        if kwargs:
            raise TypeError("unexpected keyword arguments: %r" % (kwargs,))
        return _IMap(self, func, iterables, maxsize, ordered=False)

    # -- gevent capacity API (unbounded at Group level) -----------------------

    def full(self):
        return False

    def wait_available(self, timeout=None):
        return 1


def _ready(g):
    ready = getattr(g, "ready", None)
    if ready is not None:
        return ready()
    return bool(getattr(g, "dead", False))


class _IMap(object):
    """
    Shared engine for ``imap`` / ``imap_unordered``.

    A feeder greenthread submits work through the group/pool (so pool bounds
    apply and work is EAGER); workers push ``(index, outcome)`` onto a result
    queue (bounded by ``maxsize`` for back-pressure).  Iteration decodes
    outcomes, re-raising a worker's exception at the point it is consumed.
    """

    _DONE = object()    # feeder-finished wakeup sentinel

    def __init__(self, group, func, iterables, maxsize, ordered):
        self._queue = filament.Queue(maxsize if maxsize else None)
        self._ordered = ordered
        self._total = None          # known once the feeder exhausts the input
        self._received = 0
        self._next_index = 0        # next index to yield (ordered mode)
        self._held = {}             # out-of-order buffer (ordered mode)

        def worker(index, items):
            try:
                value = func(*items)
            except BaseException:
                import sys
                self._queue.put((index, False, sys.exc_info()))
            else:
                self._queue.put((index, True, value))

        def feeder():
            count = 0
            for items in zip(*iterables):
                group.spawn(worker, count, items)
                count += 1
            self._total = count
            # Wake a consumer parked in get() so it can re-check termination.
            self._queue.put(self._DONE)

        self._feeder = filament.spawn(feeder)

    def __iter__(self):
        return self

    def _decode(self, entry):
        _index, ok, payload = entry
        if ok:
            return payload
        _greenlet._reraise(*payload)

    def __next__(self):
        while True:
            if self._ordered and self._next_index in self._held:
                entry = self._held.pop(self._next_index)
                self._next_index += 1
                self._received += 1
                return self._decode(entry)
            if self._total is not None and \
                    self._received >= self._total:
                raise StopIteration
            entry = self._queue.get()
            if entry is self._DONE:
                continue            # feeder finished; re-check termination
            if self._ordered and entry[0] != self._next_index:
                self._held[entry[0]] = entry
                continue
            if self._ordered:
                self._next_index += 1
            self._received += 1
            return self._decode(entry)

    next = __next__  # Py2


class Pool(Group):
    """A Group with a concurrency ceiling enforced by a slot semaphore."""

    def __init__(self, size=None, greenlet_class=None):
        if size is not None and size < 0:
            raise ValueError(
                "size must not be negative: %r" % (size,))
        Group.__init__(self)
        self.size = size
        if greenlet_class is not None:
            self.greenlet_class = greenlet_class
        self._semaphore = \
            filament.Semaphore(size) if size is not None else None

    # -- capacity -------------------------------------------------------------

    def free_count(self):
        """Number of slots immediately available (gevent parity)."""
        if self._semaphore is None:
            return 1    # effectively unbounded
        counter = self._semaphore.counter
        return counter if counter > 0 else 0

    def full(self):
        return self.free_count() <= 0

    def wait_available(self, timeout=None):
        """
        Block until a slot is free; return the free count, or 0 on timeout
        (gevent contract: never raises on timeout).
        """
        if self._semaphore is None:
            return 1
        # Acquire-then-release proves a slot exists without keeping it.
        if not self._semaphore.acquire(timeout=timeout):
            return 0
        self._semaphore.release()
        return self.free_count()

    # -- membership (slot-aware) ----------------------------------------------

    def add(self, greenlet_, blocking=True, timeout=None):
        """
        Track ``greenlet_``, consuming a pool slot.

        Raises :class:`PoolFull` if the pool is full and ``blocking`` is False
        (or ``timeout`` expires) -- gevent parity.
        """
        if greenlet_ in self.greenlets:
            return
        if self._semaphore is not None:
            if not self._semaphore.acquire(blocking=blocking,
                                           timeout=timeout):
                raise PoolFull()
        try:
            Group.add(self, greenlet_)
        except BaseException:
            if self._semaphore is not None:
                self._semaphore.release()
            raise

    def _discarded(self, greenlet_):
        # Called exactly once per tracked greenlet (guarded by set membership
        # in Group.discard): give its slot back.
        if self._semaphore is not None:
            self._semaphore.release()

    # -- spawning -------------------------------------------------------------

    def spawn(self, function, *args, **kwargs):
        """Spawn into the pool, blocking until a slot frees when full."""
        g = self.greenlet_class(function, *args, **kwargs)
        self.add(g)          # blocking slot acquisition
        g.start()
        return g


__all__ = ["Group", "Pool", "PoolFull"]
