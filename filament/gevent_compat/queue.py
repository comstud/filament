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
  * ``Channel``                   -> a faithful pure-Python port of gevent's
    unbuffered rendezvous channel (the C queue has no zero-buffer mode),
    implemented here on the compat ``Waiter`` + ``filament.Timeout``.
"""

from __future__ import absolute_import

import collections

import filament
from filament import pyqueue as _pyqueue
from filament import timeout as _timeout
from filament.gevent_compat import hub as _hub

Queue = filament.Queue
# gevent's JoinableQueue is just a Queue with task_done/join, which filament's
# Queue already has.
JoinableQueue = filament.Queue
# In gevent 25.x, SimpleQueue is the full former-Queue class (bounded ctor,
# iteration, ...) minus task_done/join.  filament.Queue is the closest match;
# carrying task_done/join as extras is a harmless superset.  The zero-arg C
# filament.SimpleQueue cannot even accept gevent's ``SimpleQueue(maxsize)``.
SimpleQueue = filament.Queue
PriorityQueue = _pyqueue.PriorityQueue
LifoQueue = _pyqueue.LifoQueue
Empty = filament.Empty
Full = filament.Full


def _safe_remove(deque_obj, item):
    # The item may already have been removed by the peer that woke us.
    try:
        deque_obj.remove(item)
    except ValueError:
        pass


class Channel(object):
    """
    An UNBUFFERED synchronous channel (gevent.queue.Channel).

    A ``put`` blocks until a ``get`` takes the item (and vice versa) -- there is
    no internal buffer.  This is a port of gevent's Channel onto filament's
    primitives, keeping gevent's public surface: ``getters`` / ``putters``
    deques, ``balance``, ``qsize`` / ``empty`` / ``full``, the non-blocking
    forms, timeouts raising ``Full`` / ``Empty``, and iteration terminated by a
    ``StopIteration`` sentinel value.

    One structural difference from gevent: when the peer is already waiting we
    hand the item over directly instead of parking and pairing via a scheduled
    hub callback (filament has no hub greenlet to defer to).  Net semantics are
    the same -- in particular ``put_nowait`` succeeds exactly when a getter is
    waiting, and ``get_nowait`` exactly when a putter is.
    """

    def __init__(self, maxsize=1):
        # gevent accepts (and requires) maxsize=1 to simplify generic code.
        if maxsize != 1:
            raise ValueError("Channels have a maxsize of 1")
        self.getters = collections.deque()   # Waiters parked in get()
        self.putters = collections.deque()   # (item, Waiter) parked in put()
        self.hub = _hub.get_hub()

    def __repr__(self):
        return '<%s at %s %s>' % (
            type(self).__name__, hex(id(self)), self._format())

    def __str__(self):
        return '<%s %s>' % (type(self).__name__, self._format())

    def _format(self):
        result = ''
        if self.getters:
            result += ' getters[%s]' % len(self.getters)
        if self.putters:
            result += ' putters[%s]' % len(self.putters)
        return result

    @property
    def balance(self):
        return len(self.putters) - len(self.getters)

    def qsize(self):
        return 0

    def empty(self):
        return True

    def full(self):
        return True

    def put(self, item, block=True, timeout=None):
        if self.getters:
            getter = self.getters.popleft()
            getter.switch(item)
            return
        if not block:
            raise Full
        waiter = _hub.Waiter()
        entry = (item, waiter)
        self.putters.append(entry)
        timer = _timeout.Timeout(timeout, Full)
        timer.start()
        try:
            waiter.get()
        except BaseException:
            _safe_remove(self.putters, entry)
            raise
        finally:
            timer.cancel()

    def put_nowait(self, item):
        self.put(item, False)

    def get(self, block=True, timeout=None):
        if self.putters:
            item, putter = self.putters.popleft()
            putter.switch(None)
            return item
        if not block:
            raise Empty
        waiter = _hub.Waiter()
        self.getters.append(waiter)
        timer = _timeout.Timeout(timeout, Empty)
        timer.start()
        try:
            return waiter.get()
        except BaseException:
            _safe_remove(self.getters, waiter)
            raise
        finally:
            timer.cancel()

    def get_nowait(self):
        return self.get(False)

    def __iter__(self):
        return self

    def __next__(self):
        result = self.get()
        if result is StopIteration:
            raise result
        return result

    next = __next__  # Py2


__all__ = ["Queue", "JoinableQueue", "SimpleQueue", "PriorityQueue",
           "LifoQueue", "Channel", "Empty", "Full"]
