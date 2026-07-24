# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.lock
===========================

Drop-in replacement for ``gevent.lock`` (injected as
``sys.modules['gevent.lock']``).

Provides the gevent lock family backed by filament's cooperative locking:

  * ``Semaphore``        -> :class:`filament.Semaphore` (faithful mapping).
  * ``BoundedSemaphore`` -> wrapper adding the release-ceiling check.
  * ``RLock``            -> :class:`filament.RLock` (faithful mapping).
  * ``DummySemaphore``   -> a no-op semaphore that never blocks (gevent shape).
"""

from __future__ import absolute_import

import filament

# Reuse the same BoundedSemaphore implementation the eventlet shim uses -- the
# semantics are identical, so there's no reason to duplicate it.
from filament.eventlet_compat.semaphore import BoundedSemaphore

Semaphore = filament.Semaphore
RLock = filament.RLock


class DummySemaphore(object):
    """
    A semaphore that is always available (gevent.lock.DummySemaphore).

    Useful as a "no concurrency limit" stand-in: acquire always succeeds
    instantly and release does nothing.  Faithful mapping of gevent's dummy.
    """

    def __init__(self, value=1):
        # value is accepted for API parity but ignored -- it is unbounded.
        pass

    def acquire(self, blocking=True, timeout=None):
        return True

    def release(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, typ, value, tb):
        return False


__all__ = ["Semaphore", "BoundedSemaphore", "RLock", "DummySemaphore"]
