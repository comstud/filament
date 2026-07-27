# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.main
===========================

The top-level ``gevent`` namespace (injected as ``sys.modules['gevent']``).

Re-exports filament-backed implementations under the names gevent programs
expect: ``spawn`` / ``spawn_later`` / ``spawn_raw`` / ``sleep`` / ``getcurrent``,
``joinall`` / ``killall`` / ``wait`` / ``iwait`` / ``kill``, ``Greenlet``,
``Timeout``, ``GreenletExit`` and ``get_hub``.  All are faithful mappings unless
flagged otherwise in the backing module.
"""

from __future__ import absolute_import

import signal as _signal

import filament

from filament.gevent_compat import greenlet as _greenlet
from filament.gevent_compat import rawgreenlet as _rawgreenlet
from filament.gevent_compat import hub as _hub

# -- greenlet spawning / control (faithful mappings) ------------------------
Greenlet = _greenlet.Greenlet
spawn = _greenlet.spawn
spawn_later = _greenlet.spawn_later
spawn_raw = _greenlet.spawn_raw
kill = _greenlet.kill
killall = _greenlet.killall
joinall = _greenlet.joinall
wait = _greenlet.wait
iwait = _greenlet.iwait
GreenletExit = _greenlet.GreenletExit

# -- sleeping / current greenlet --------------------------------------------
def sleep(seconds=0, ref=True):
    """gevent.sleep; ``ref`` is accepted for parity and ignored (no libev)."""
    return filament.sleep(seconds)


# gevent.getcurrent IS greenlet.getcurrent; keep the two answers identical so
# ``gevent.getcurrent() is <the Greenlet you were handed>`` holds here too.
getcurrent = _rawgreenlet.getcurrent
idle = lambda priority=0: filament.sleep(0)  # noqa: E731 - gevent.idle parity

# -- timeouts (faithful mapping) --------------------------------------------
Timeout = filament.Timeout
with_timeout = filament.with_timeout

# -- hub --------------------------------------------------------------------
get_hub = _hub.get_hub


# -- signals ----------------------------------------------------------------
class _SignalHandler(object):
    """
    The cancellable handle ``gevent.signal_handler`` hands back.

    gevent runs the handler in a fresh greenlet off its hub; we spawn a
    greenthread from the stdlib handler, which filament's scheduler runs at
    its next signal check.  ``cancel()`` restores whatever handler was
    installed before, as gevent's does.
    """

    def __init__(self, signalnum, handler, args, kwargs):
        self.signalnum = signalnum
        self.handler = handler
        self.args = args
        self.kwargs = kwargs
        self.ref = True                  # gevent attribute; inert here
        self._previous = _signal.getsignal(signalnum)
        _signal.signal(signalnum, self._deliver)

    def _deliver(self, signalnum, frame):
        handler = self.handler
        if handler is not None:
            filament.spawn_n(handler, *self.args, **self.kwargs)

    def cancel(self):
        """Uninstall the handler (idempotent)."""
        if self.handler is None:
            return
        self.handler = None
        try:
            _signal.signal(self.signalnum, self._previous)
        except (ValueError, TypeError, OSError):  # pragma: no cover
            # Not on the main thread any more, or the saved handler is not
            # settable (a C-level default): leave the current one in place.
            pass

    stop = cancel


def signal_handler(signalnum, handler, *args, **kwargs):
    """
    gevent.signal_handler: run ``handler`` in a greenthread on ``signalnum``.

    Returns a handle with ``cancel()``.  Applications install their SIGTERM
    handler this way, so a missing one takes the whole process down.
    """
    return _SignalHandler(signalnum, handler, args, kwargs)


# gevent kept the old spelling around as an alias for a long time.
signal = signal_handler

# gevent exposes its version here; provide a marker so ``gevent.__version__``
# reads (some libraries branch on it).  We flag this as a filament shim.
__version__ = "filament-compat"


__all__ = ["Greenlet", "spawn", "spawn_later", "spawn_raw", "kill", "killall",
           "joinall", "wait", "iwait", "GreenletExit", "sleep", "getcurrent",
           "idle", "Timeout", "with_timeout", "get_hub", "signal_handler",
           "signal", "__version__"]
