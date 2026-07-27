# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.event
==============

One-shot / result primitives:

  * :class:`Event`       -- a re-settable flag many greenthreads can wait on
                            (gevent.event.Event / threading.Event shape).
  * :class:`AsyncResult` -- a one-shot value-or-exception future
                            (gevent.event.AsyncResult, and also serves as the
                            eventlet ``event.Event`` via send/send_exception/
                            reset aliases).

Design choice -- Message vs Condition
-------------------------------------
``AsyncResult`` is built on the C ``_filament.core.Message`` future, NOT on a
Condition.  ``Message`` is *exactly* a one-shot future already: it stores a
result (or an exception with traceback), wakes *all* waiters when set, caches
the result so late waiters return immediately, supports a wait timeout, and --
critically -- ``Message.wait()`` faithfully re-raises the stored exception with
its original traceback.  Re-implementing that on a Condition + Python state
would be strictly more code and easy to get subtly wrong.  ``Message`` can only
be sent once, which is precisely the one-shot contract we want; the only place
we need re-settability (eventlet's ``Event.reset()``) we handle by swapping in a
fresh ``Message``.

``Event`` (the flag) *is* built on a Condition, because it needs clear()/re-set
semantics and "many waiters, all released on set" -- a natural fit for
``notify_all()``.
"""

from __future__ import absolute_import

import sys

from _filament.core import Message
from _filament.locking import Condition

from filament import exc
from filament import greenthread
from filament import timeout as _timeout

# The C primitives raise the *base* ``exc.Timeout`` on a wait timeout; the
# context-manager ``filament.timeout.Timeout`` (== gevent.Timeout in the shim)
# is a subclass.  ``type(e) is exc.Timeout`` therefore identifies "our own wait
# timed out" vs "an outer ``with Timeout(...):`` fired", which must propagate.


# -- small py2/py3 re-raise helper ------------------------------------------
if sys.version_info[0] >= 3:
    def _reraise(exc_type, exc_value, exc_tb):
        if exc_value is None:
            exc_value = exc_type()
        if exc_tb is not None and exc_value.__traceback__ is not exc_tb:
            raise exc_value.with_traceback(exc_tb)
        raise exc_value
else:  # pragma: no cover -- py2-only
    # exec keeps the py2-only 3-arg raise syntax out of the py3 parser.
    exec("def _reraise(exc_type, exc_value, exc_tb):\n"
         "    raise exc_type, exc_value, exc_tb\n")


class Event(object):
    """
    A cooperative, re-settable event flag.

    Many greenthreads may :meth:`wait`; a single :meth:`set` releases them all.
    :meth:`clear` returns it to the unset state so it can be reused.

    Built on a C ``Condition``.  ``set()`` is safe to call from the scheduler
    thread (e.g. a timer callback): waiters release the Condition's lock while
    parked in ``wait()``, so ``set()``'s brief acquire never blocks the
    scheduler.
    """

    def __init__(self):
        self._cond = Condition()
        self._flag = False
        # Monotonic count of set() calls, so waiters can tell "the flag was
        # set (and possibly cleared again) while I was parked" -- gevent 1.1
        # semantics: such a waiter still sees True.
        self._set_count = 0

    def is_set(self):
        """Return True if the event is set."""
        return self._flag

    # gevent/eventlet spell this ``ready``; threading spells it ``is_set``.
    ready = is_set

    def isSet(self):  # noqa: N802 (legacy threading alias)
        return self._flag

    def set(self):
        """Set the flag and wake every waiter."""
        with self._cond:
            self._flag = True
            self._set_count += 1
            self._cond.notify_all()

    def clear(self):
        """Reset the flag to unset (waiters after this will block again)."""
        with self._cond:
            self._flag = False

    def wait(self, timeout=None):
        """
        Block until the flag is set, or until ``timeout`` seconds elapse.

        Returns the flag value at wakeup: True if it was set, False on timeout.
        Never raises on timeout (matches threading.Event).
        """
        with self._cond:
            if self._flag:
                return True
            start_count = self._set_count
            try:
                # Condition.wait releases the lock, parks, reacquires on wake.
                self._cond.wait(timeout)
            except exc.Timeout as e:
                if type(e) is not exc.Timeout:
                    raise          # an outer with-Timeout fired; propagate
                return self._set_count != start_count
            # True if currently set, OR if it was set at any point while we
            # were parked (even if cleared again before we resumed) -- gevent
            # 1.1 semantics.
            return self._flag or self._set_count != start_count


# Sentinel distinguishing "no value yet" from a legitimately stored None.
_NO_VALUE = object()


class AsyncResult(object):
    """
    A one-shot future holding either a value or an exception.

    gevent API: :meth:`set`, :meth:`set_exception`, :meth:`get`,
    :meth:`get_nowait`, :meth:`wait`, :meth:`ready`, :meth:`successful`,
    :attr:`value`, :attr:`exception`, :meth:`link`.

    eventlet ``event.Event`` API (same object): :meth:`send`,
    :meth:`send_exception`, :meth:`wait`, :meth:`ready`, :meth:`reset`.

    Implemented on top of ``_filament.core.Message`` (see module docstring).
    """

    def __init__(self):
        self._msg = Message()
        self._value = None
        # (type, value, tb) once an exception has been stored, else None.
        self._exc_info = None
        self._ready = False
        self._links = []

    # -- state inspection ----------------------------------------------------

    def ready(self):
        """True once a value or exception has been stored."""
        return self._ready

    def successful(self):
        """True if ready AND it holds a value (not an exception)."""
        return self._ready and self._exc_info is None

    @property
    def value(self):
        """The stored value (None if not ready or holding an exception)."""
        return self._value

    @property
    def exception(self):
        """The stored exception instance, or None."""
        return self._exc_info[1] if self._exc_info else None

    @property
    def exc_info(self):
        """The stored ``(type, value, tb)`` tuple if failed (gevent parity)."""
        if self._exc_info:
            return self._exc_info

    # -- setting the result --------------------------------------------------

    def set(self, value=None):
        """
        Store ``value`` and wake all waiters.

        Like gevent, calling this on an already-set result silently overwrites
        the previous value (or previously stored exception).  Waiters that were
        already released keep the value they saw; there can be no parked
        waiters once the result is ready.
        """
        was_ready = self._ready
        self._value = value
        self._exc_info = None
        self._ready = True
        # Links first, waiters second, and do not reorder.  gevent notifies
        # both from one ordered list, so a link registered before a get() has
        # run by the time that get() returns; the scheduler's immediate queue
        # is FIFO, so links must be scheduled before waiters are woken.
        try:
            self._fire_links()
        finally:
            if not was_ready:
                self._msg.send(value)  # wakes/re-arms all Message waiters

    def set_exception(self, exception, exc_info=None):
        """
        Store an exception (so waiters re-raise it).  Overwrites like ``set``.

        ``exc_info`` may be an explicit ``(type, value, tb)`` tuple to preserve
        a traceback; otherwise we synthesize one from ``exception``.
        """
        if exc_info is None:
            exc_info = (type(exception), exception, None)
        was_ready = self._ready
        self._exc_info = exc_info
        self._value = None
        self._ready = True
        try:
            self._fire_links()          # links before waiters; see set()
        finally:
            if not was_ready:
                self._msg.send_exception(exc_info[0], exc_info[1],
                                         exc_info[2])

    # -- getting the result --------------------------------------------------

    def get(self, block=True, timeout=None):
        """
        Return the value, or re-raise the stored exception.

        With ``block=False`` and no result yet, raise
        :class:`filament.exc.Timeout` immediately.  With ``block=True`` we park
        (cooperatively) until set, or raise Timeout after ``timeout`` seconds.
        """
        if self._ready:
            if self._exc_info is not None:
                _reraise(*self._exc_info)
            return self._value
        if not block:
            # gevent raises its Timeout class here; ours is _timeout.Timeout.
            raise _timeout.Timeout(exception=None)
        # Message.wait returns the value or re-raises the stored exception (with
        # its original traceback), and raises exc.Timeout on wait timeout.
        try:
            return self._msg.wait(timeout)
        except exc.Timeout as e:
            if type(e) is exc.Timeout:
                # Our own wait timed out: surface it as the gevent-catchable
                # subclass (``except gevent.Timeout:`` must catch this).
                raise _timeout.Timeout(timeout)
            raise                      # an outer with-Timeout fired; propagate

    def get_nowait(self):
        """Non-blocking :meth:`get`; raises Timeout if not ready."""
        return self.get(block=False)

    def wait(self, timeout=None):
        """
        Block until ready and return the *value*.

        Unlike :meth:`get`, this never re-raises a stored exception and returns
        ``None`` on timeout -- matching gevent's ``AsyncResult.wait``.
        """
        if not self._ready:
            try:
                self._msg.wait(timeout)
            except exc.Timeout as e:
                if type(e) is exc.Timeout:
                    return None        # our own wait timed out
                raise                  # an outer with-Timeout fired; propagate
            except Exception:
                # An exception result: wait() reports the value (None) rather
                # than raising.  Callers wanting the exception use get().
                pass
        return self._value

    # -- callbacks -----------------------------------------------------------

    def __call__(self, source):
        """
        gevent link-target protocol: adopt ``source``'s outcome as our own.

        Lets an AsyncResult be passed straight to ``greenlet.link(result)``.
        """
        if source.successful():
            self.set(source.value)
        else:
            exc_info = getattr(source, "exc_info", None)
            self.set_exception(source.exception, exc_info or None)
        return self

    def link(self, callback):
        """
        Arrange for ``callback(self)`` to be invoked once this becomes ready.

        If already ready, the callback is scheduled right away.  Callbacks run
        in their own fire-and-forget greenthread so a slow or raising callback
        can never disrupt the setter (this mirrors gevent running links in the
        hub).
        """
        self._links.append(callback)
        if self._ready:
            self._fire_links()

    def _fire_links(self):
        if not self._links:
            return
        links = self._links
        self._links = []
        for cb in links:
            greenthread.spawn_n(cb, self)

    # -- eventlet event.Event compatibility ---------------------------------
    #
    # eventlet's Event uses send/send_exception/reset.  We expose them on the
    # same object so the later eventlet shim can map ``eventlet.event.Event``
    # straight onto ``AsyncResult``.

    def send(self, result=None, exc_obj=None):
        """
        eventlet alias: deliver ``result`` (or an exception via ``exc_obj``).

        Unlike gevent's ``set`` (which overwrites), eventlet forbids re-sending
        an un-reset Event -- keep that contract here.
        """
        assert not self._ready, "Trying to re-send() an already-sent event."
        if exc_obj is not None:
            return self.set_exception(exc_obj)
        return self.set(result)

    def send_exception(self, *args):
        """
        eventlet alias for delivering an exception.  Accepts either a single
        exception instance or a ``(type, value, tb)`` triple.
        """
        if len(args) == 1:
            return self.set_exception(args[0])
        exc_type = args[0]
        exc_value = args[1] if len(args) > 1 else None
        exc_tb = args[2] if len(args) > 2 else None
        if exc_value is None:
            exc_value = exc_type()
        return self.set_exception(exc_value, (exc_type, exc_value, exc_tb))

    def reset(self):
        """
        eventlet alias: discard the result so the Event can be reused.

        Because ``Message`` is strictly one-shot, we swap in a fresh one.
        """
        self._msg = Message()
        self._value = None
        self._exc_info = None
        self._ready = False
        self._links = []
