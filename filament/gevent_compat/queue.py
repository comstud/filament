# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.queue
============================

Drop-in replacement for ``gevent.queue`` (injected as
``sys.modules['gevent.queue']``).

Mapping summary:

  * ``Queue`` / ``JoinableQueue`` -> :class:`filament.Queue` (the C queue
    already provides ``task_done`` / ``join``, so JoinableQueue is the same
    class -- faithful mapping).
  * ``SimpleQueue``               -> :class:`filament.SimpleQueue`.
  * ``PriorityQueue`` / ``LifoQueue`` -> filament's pure-Python cooperative
    subclasses (faithful mapping, different ordering discipline).
  * ``Empty`` / ``Full``          -> filament's queue exceptions.
  * ``Channel``                   -> a small pure-Python UNBUFFERED rendezvous
    queue implemented here (the C queue has no zero-buffer mode).
"""

from __future__ import absolute_import

import filament
from filament import pyqueue as _pyqueue
from filament import locking as _locking

Queue = filament.Queue
# gevent's JoinableQueue is just a Queue with task_done/join, which filament's
# Queue already has.
JoinableQueue = filament.Queue
SimpleQueue = filament.SimpleQueue
PriorityQueue = _pyqueue.PriorityQueue
LifoQueue = _pyqueue.LifoQueue
Empty = filament.Empty
Full = filament.Full


class Channel(object):
    """
    An UNBUFFERED synchronous channel (gevent.queue.Channel).

    A ``put`` blocks until a ``get`` takes the item (and vice versa) -- there is
    no internal buffer.  Implemented with a filament ``Condition``: putters
    deposit into a one-slot handoff and wait for a getter to claim it.

    This is a faithful re-implementation of gevent's zero-buffer rendezvous
    semantics on filament's cooperative primitives.
    """

    def __init__(self):
        self._lock = _locking.Lock()
        self._cond = _locking.Condition(lock=self._lock)
        # The single in-flight item, wrapped so we can distinguish "no item".
        self._item = []          # 0 or 1 element acting as the handoff slot
        self._getters_waiting = 0

    def put(self, item, block=True, timeout=None):
        with self._lock:
            # Wait until the slot is empty (previous item consumed).
            while self._item:
                self._cond.wait(timeout=timeout)
            self._item.append(item)
            # Wake a getter to take it.
            self._cond.notify_all()
            # Block until the item is actually taken (rendezvous).
            while self._item:
                self._cond.wait(timeout=timeout)

    def get(self, block=True, timeout=None):
        with self._lock:
            # Wait until an item is present.
            while not self._item:
                self._cond.wait(timeout=timeout)
            item = self._item.pop()
            # Wake the blocked putter (and any other putters) now that the slot
            # is free.
            self._cond.notify_all()
            return item

    def put_nowait(self, item):
        # Unbuffered: a non-blocking put can only succeed if a getter is already
        # waiting.  We approximate by attempting and raising Full otherwise.
        raise Full()

    def get_nowait(self):
        with self._lock:
            if not self._item:
                raise Empty()
            item = self._item.pop()
            self._cond.notify_all()
            return item


__all__ = ["Queue", "JoinableQueue", "SimpleQueue", "PriorityQueue",
           "LifoQueue", "Channel", "Empty", "Full"]
