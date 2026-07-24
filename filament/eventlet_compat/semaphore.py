# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.eventlet_compat.semaphore
==================================

Drop-in replacement for ``eventlet.semaphore`` (injected as
``sys.modules['eventlet.semaphore']``).

Provides ``Semaphore`` and ``BoundedSemaphore`` backed by filament's
cooperative C ``Semaphore``.  A plain ``Semaphore`` is a faithful mapping;
``BoundedSemaphore`` is a small Python wrapper that adds the "never release
above the initial value" check on top (the C primitive has no bound concept).
"""

from __future__ import absolute_import

import filament

# eventlet.semaphore.Semaphore -> filament's cooperative Semaphore directly.
Semaphore = filament.Semaphore


class BoundedSemaphore(object):
    """
    A Semaphore that refuses to be released beyond its initial count.

    We wrap (rather than subclass) the C Semaphore and track the current count
    ourselves so ``release`` past the ceiling raises ValueError, matching both
    threading.BoundedSemaphore and eventlet.  Acquire/release still block/wake
    cooperatively via the underlying filament Semaphore.
    """

    def __init__(self, value=1):
        self._initial = value
        self._sem = filament.Semaphore(value)
        # Mirror of the permit count, guarded by cooperative scheduling (single
        # OS thread) -- no OS lock needed between greenthreads.
        self._count = value

    def acquire(self, blocking=True, timeout=None):
        # filament's Semaphore.acquire signature is acquire(timeout=None); we
        # honour the eventlet/threading (blocking, timeout) call shape.
        if not blocking:
            timeout = 0
        acquired = self._sem.acquire(timeout=timeout)
        # The C primitive returns None on success / raises on timeout; normalise
        # to a bool and keep our mirror in step.
        self._count -= 1
        return True if acquired is None else acquired

    def release(self):
        if self._count >= self._initial:
            raise ValueError("Semaphore released too many times")
        self._count += 1
        return self._sem.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, typ, value, tb):
        self.release()


__all__ = ["Semaphore", "BoundedSemaphore"]
