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

try:
    # Python 3: filament's private vendored greenlet runtime.  All of
    # filament's switching happens on this runtime, so getcurrent() /
    # GreenletExit must come from it, not from an installed greenlet.
    import _fil_greenlet as greenlet
except ImportError:  # Python 2 / stock-greenlet build
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
        if self._target.dead:
            # Fired after the target already finished: nothing to interrupt.
            # Throwing into a dead greenlet re-raises the exception right
            # here in the timer callback, where it can only be reported as
            # unraisable noise.
            return
        exception = self.exception
        if exception is None or exception is False or \
                not (isinstance(exception, BaseException) or
                     (isinstance(exception, type) and
                      issubclass(exception, BaseException))):
            # Default, silent-sentinel, or a non-exception payload (gevent
            # allows e.g. a string message): raise ourselves; __str__ carries
            # the payload.  The silent case is suppressed in __exit__.
            thrown = self
        else:
            # Raise the caller supplied exception (class or instance).
            thrown = exception
        try:
            self._target.throw(thrown)
        except BaseException as e:
            # The target did not catch what we threw and its greenlet died
            # unwinding; greenlet re-raises the escaping exception here, in
            # the thrower.  Our own timeout coming back is DELIVERY, not an
            # error -- a timeout is allowed to kill its greenthread (gevent
            # ignores this the same way).  Anything else is a genuine error
            # from the dying target: let it out, and the timer machinery
            # reports it as unraisable.
            if not (e is thrown or
                    (isinstance(thrown, type) and isinstance(e, thrown))):
                raise

    def cancel(self):
        """Disarm the timeout if it has not fired yet."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def close(self):
        """
        Disarm and release the timeout (gevent parity).

        gevent's ``close()`` cancels and returns the object to its pool; we
        have nothing to pool, so cancelling is the whole job.  Libraries call
        this in ``finally`` blocks -- pyzmq's green sockets do it around every
        send and recv -- so it has to exist.
        """
        self.cancel()

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
        if self.exception is None or self.exception is False or \
                isinstance(self.exception, BaseException) or \
                isinstance(self.exception, type):
            return "%s seconds" % (self.seconds,)
        # Non-exception payload (e.g. a message string) -- gevent format.
        return "%s seconds: %s" % (self.seconds, self.exception)


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
