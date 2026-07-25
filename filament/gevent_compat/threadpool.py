# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.threadpool
=================================

Drop-in-ish replacement for ``gevent.threadpool`` (injected as
``sys.modules['gevent.threadpool']``).

gevent's ``ThreadPool`` runs blocking callables on real OS threads while only
the calling greenlet waits.  filament's ``filament.tpool`` does exactly that, so
:class:`ThreadPool` here is a thin gevent-shaped facade over it.

Mappings:
  * ``apply``          -> ``filament.tpool.execute`` (blocking) -- faithful.
  * ``spawn``          -> run ``apply`` inside a :class:`Greenlet` so the caller
                          gets a future with ``.get()`` -- faithful.
  * ``map`` / ``imap`` -> concurrent spawn + ordered collection -- faithful.

Divergence: gevent's ``maxsize`` maps onto filament's default thread pool size
via :func:`filament.tpool.set_num_threads`.  Because filament's tpool default
pool is process-global, constructing multiple ThreadPools with different sizes
all share (and resize) that one pool -- documented limitation.
"""

from __future__ import absolute_import

import filament
from filament import tpool as _tpool

from filament.gevent_compat import greenlet as _greenlet_mod
from filament.gevent_compat.greenlet import Greenlet


class ThreadPool(object):
    """gevent-shaped facade over :mod:`filament.tpool`."""

    def __init__(self, maxsize=None, hub=None):
        self.maxsize = maxsize
        self._outstanding = 0
        self._idle = filament.Event()
        self._idle.set()
        if maxsize is not None:
            # Resize filament's shared default pool.  See module docstring re:
            # the global-pool limitation.
            _tpool.set_num_threads(maxsize)

    def apply(self, func, args=None, kwds=None):
        """Run ``func(*args, **kwds)`` in a worker thread; block for result."""
        args = args or ()
        kwds = kwds or {}
        return _tpool.execute(func, *args, **kwds)

    def _task_finished(self, _g):
        self._outstanding -= 1
        if self._outstanding == 0:
            self._idle.set()

    def spawn(self, func, *args, **kwargs):
        """
        Run ``func`` in a worker thread, returning a :class:`Greenlet` future.

        ``.get()`` on the returned greenlet yields the thread's result (or
        re-raises its exception).
        """
        self._outstanding += 1
        self._idle.clear()
        g = Greenlet.spawn(_tpool.execute, func, *args, **kwargs)
        g.link(self._task_finished)
        return g

    def map(self, func, iterable):
        """Concurrently apply ``func`` to each item; return an ordered list."""
        greenlets = [self.spawn(func, item) for item in iterable]
        return [g.get() for g in greenlets]

    @staticmethod
    def _pop_maxsize(kwargs):
        # gevent's imap/imap_unordered take a ``maxsize`` kwarg bounding the
        # result buffer.  We accept it for parity; filament's shared tpool
        # provides its own back-pressure, so it is not otherwise used.
        kwargs.pop("maxsize", None)
        if kwargs:
            raise TypeError("unexpected keyword arguments: %r" % (kwargs,))

    def imap(self, func, *iterables, **kwargs):
        """Ordered concurrent map over one or more iterables (gevent shape)."""
        self._pop_maxsize(kwargs)
        greenlets = [self.spawn(func, *items) for items in zip(*iterables)]

        def _gen():
            for g in greenlets:
                yield g.get()
        return _gen()

    def imap_unordered(self, func, *iterables, **kwargs):
        """Concurrent map yielding results in COMPLETION order."""
        self._pop_maxsize(kwargs)
        greenlets = [self.spawn(func, *items) for items in zip(*iterables)]

        def _gen():
            for g in _greenlet_mod.iwait(list(greenlets)):
                yield g.get()
        return _gen()

    def join(self):
        """Block until every task spawned so far has finished (gevent)."""
        self._idle.wait()

    def kill(self):
        """Shut filament's default thread pool down."""
        _tpool.shutdown()

    @property
    def size(self):
        """Configured thread count (gevent reports live threads; we report
        the configured size -- filament manages the actual threads)."""
        return self.maxsize or 0

    # gevent's __len__ is the number of unfinished tasks.
    def __len__(self):
        return self._outstanding


__all__ = ["ThreadPool"]
