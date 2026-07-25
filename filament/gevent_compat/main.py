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

import filament

from filament.gevent_compat import greenlet as _greenlet
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


getcurrent = filament.getcurrent
idle = lambda priority=0: filament.sleep(0)  # noqa: E731 - gevent.idle parity

# -- timeouts (faithful mapping) --------------------------------------------
Timeout = filament.Timeout
with_timeout = filament.with_timeout

# -- hub --------------------------------------------------------------------
get_hub = _hub.get_hub

# gevent exposes its version here; provide a marker so ``gevent.__version__``
# reads (some libraries branch on it).  We flag this as a filament shim.
__version__ = "filament-compat"


__all__ = ["Greenlet", "spawn", "spawn_later", "spawn_raw", "kill", "killall",
           "joinall", "wait", "iwait", "GreenletExit", "sleep", "getcurrent",
           "idle", "Timeout", "with_timeout", "get_hub", "__version__"]
