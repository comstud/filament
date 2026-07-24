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

from filament.gevent_compat.greenlet import Greenlet


class ThreadPool(object):
    """gevent-shaped facade over :mod:`filament.tpool`."""

    def __init__(self, maxsize=None, hub=None):
        self.maxsize = maxsize
        if maxsize is not None:
            # Resize filament's shared default pool.  See module docstring re:
            # the global-pool limitation.
            _tpool.set_num_threads(maxsize)

    def apply(self, func, args=None, kwds=None):
        """Run ``func(*args, **kwds)`` in a worker thread; block for result."""
        args = args or ()
        kwds = kwds or {}
        return _tpool.execute(func, *args, **kwds)

    def spawn(self, func, *args, **kwargs):
        """
        Run ``func`` in a worker thread, returning a :class:`Greenlet` future.

        ``.get()`` on the returned greenlet yields the thread's result (or
        re-raises its exception).
        """
        return Greenlet.spawn(_tpool.execute, func, *args, **kwargs)

    def map(self, func, iterable):
        """Concurrently apply ``func`` to each item; return an ordered list."""
        greenlets = [self.spawn(func, item) for item in iterable]
        return [g.get() for g in greenlets]

    def imap(self, func, iterable):
        """Lazy ordered concurrent map (yields results in input order)."""
        greenlets = [self.spawn(func, item) for item in iterable]
        for g in greenlets:
            yield g.get()

    def imap_unordered(self, func, iterable):
        """
        Lazy concurrent map yielding results as greenlets are collected.

        We collect in spawn order (each ``.get()`` returns as soon as *that*
        item is done); because all share one scheduler nothing is starved.
        """
        greenlets = [self.spawn(func, item) for item in iterable]
        for g in greenlets:
            yield g.get()

    def join(self):
        """No-op-ish: filament's tpool has no external join; documented stub."""
        # filament.tpool delivers results per-call, so there's nothing to join
        # at the pool level.  Provided for API parity.
        return None

    def kill(self):
        """Shut filament's default thread pool down."""
        _tpool.shutdown()

    # gevent exposes the count of idle/running threads; we don't track those
    # (filament manages them internally), so report the configured size.
    def __len__(self):
        return self.maxsize or 0


__all__ = ["ThreadPool"]
