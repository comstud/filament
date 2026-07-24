# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.timeout
================

A cooperative ``Timeout`` primitive (gevent/eventlet parity).

``Timeout`` is both an *exception* and a *context manager*::

    with Timeout(5):
        some_blocking_operation()      # raises Timeout after 5s

    # the "silent" sentinel form -- on expiry the block simply exits:
    with Timeout(5, False):
        some_blocking_operation()      # returns after 5s, no exception

How the timeout is delivered (the important invariant)
------------------------------------------------------
Filament is a *cooperative* / greenlet based runtime.  A greenlet only ever
stops running when it voluntarily switches back to the scheduler (by sleeping,
waiting on a lock/message, doing I/O, etc.).  To interrupt such a blocked
greenlet we must ``throw()`` an exception *into* it -- but ``throw()`` performs
a greenlet switch and therefore MUST happen on the greenlet's own OS thread,
driven by that thread's scheduler.  Doing it from another thread would corrupt
the greenlet stack.

We get that guarantee "for free" from the C ``_filament.timer.Timer``:

  * ``Timer(seconds, cb)`` registers ``cb`` with the scheduler of *the thread
    that constructs the Timer*.
  * When it fires, ``cb`` runs on that same thread's scheduler greenlet.

So in ``start()`` we (a) capture ``greenlet.getcurrent()`` -- the greenlet that
is entering the timeout -- and (b) build the Timer *from that same greenlet*
(hence same thread).  When the Timer later fires, its callback runs on the
correct scheduler and can safely ``throw()`` into the captured target.  This is
exactly the pattern gevent uses with its hub.
"""

from __future__ import absolute_import

import greenlet

from _filament.timer import Timer
from filament import exc


# Sentinel used by ``with_timeout`` to distinguish "no timeout_value supplied"
# from an explicit ``timeout_value=None``.
_NONE = object()


# We subclass the C runtime's timeout exception (``filament.exc.Timeout``) so a
# single ``except filament.exc.Timeout`` clause catches BOTH low level wait
# timeouts (raised by the C locking/message primitives) and expiries of this
# context-manager Timeout.  gevent's ``Timeout`` is likewise an Exception.
class Timeout(exc.Timeout):
    """
    Raise an exception in the current greenlet after ``seconds``.

    :param seconds: how long to wait before firing.  ``None`` means "never
        expire" -- the Timeout becomes an inert no-op (matches gevent).
    :param exception: what to raise on expiry.
        * ``None`` (default) -> raise *this* Timeout instance.
        * ``False`` -> raise this Timeout instance too, but the context manager
          silently swallows it, so the ``with`` block just exits.  This is
          gevent's ``Timeout(seconds, False)`` idiom.
        * anything else -> raise that object/class.
    """

    def __init__(self, seconds=None, exception=None):
        # NOTE: we intentionally do NOT call the base Exception __init__ with a
        # message; a bare Timeout instance reads fine and gevent does the same.
        exc.Timeout.__init__(self)
        self.seconds = seconds
        self.exception = exception
        # The scheduler Timer, live only while pending.
        self._timer = None
        # The greenlet to interrupt -- captured at start() time.
        self._target = None

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        """Arm the timeout.  Safe to call at most once per (re)use."""
        if self._timer is not None:
            raise RuntimeError("Timeout is already started")
        if self.seconds is None:
            # A None timeout never expires -- nothing to schedule.
            return
        # Capture the greenlet that is arming the timeout.  Its scheduler (the
        # scheduler of THIS thread) is the one the Timer will fire on, so the
        # eventual throw() is guaranteed to be a same-thread greenlet switch.
        self._target = greenlet.getcurrent()
        self._timer = Timer(self.seconds, self._on_timeout)

    def _on_timeout(self):
        # Runs on the scheduler greenlet of self._target's thread (see module
        # docstring).  Throwing here is therefore a legal cooperative switch.
        if self._timer is None:
            # Cancelled between firing and running -- ignore.
            return
        self._timer = None
        exception = self.exception
        if exception is None or exception is False:
            # Default / silent-sentinel: raise ourselves.  The silent case is
            # suppressed in __exit__.
            self._target.throw(self)
        else:
            # Raise the caller supplied exception (class or instance).
            self._target.throw(exception)

    def cancel(self):
        """Disarm the timeout if it has not fired yet."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    @property
    def pending(self):
        """True while the timeout is armed and has not fired/been cancelled."""
        return self._timer is not None

    # -- context manager -----------------------------------------------------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, typ, value, tb):
        self.cancel()
        # Silent-sentinel: swallow *our own* expiry so the with-block just ends.
        # We check identity (``value is self``) so we never accidentally eat a
        # different Timeout raised by inner code.
        if value is self and self.exception is False:
            return True
        return False

    def __repr__(self):
        return "<Timeout at 0x%x seconds=%r pending=%r>" % (
            id(self), self.seconds, self.pending)

    def __str__(self):
        if self.seconds is None:
            return ""
        return "%s seconds" % (self.seconds,)


def with_timeout(seconds, function, *args, **kwargs):
    """
    Call ``function(*args, **kwargs)`` under a ``Timeout(seconds)``.

    If a ``timeout_value`` keyword is supplied and the timeout fires, that value
    is returned instead of the Timeout being raised.  (We pop it from kwargs
    rather than using a keyword-only argument so this stays Python 2.7 clean.)
    """
    timeout_value = kwargs.pop("timeout_value", _NONE)
    timeout = Timeout(seconds)
    timeout.start()
    try:
        try:
            return function(*args, **kwargs)
        except Timeout as ex:
            # Only intercept *our* timeout, and only if a fallback was given.
            if ex is timeout and timeout_value is not _NONE:
                return timeout_value
            raise
    finally:
        timeout.cancel()
