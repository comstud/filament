# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament
========

A greenlet-based cooperative concurrency library.  This package exposes a
clean, filament-native high-level API (the gevent/eventlet-equivalent
primitives) built in pure Python on top of the compiled ``_filament`` C core.

Everything here is safe to use as ``filament.spawn``, ``filament.Event``,
``filament.GreenPool``, ``filament.Timeout``, etc.
"""

from __future__ import absolute_import

# ---------------------------------------------------------------------------
# C core.  These are the low-level primitives the pure-Python layer builds on:
#   spawn, sleep, yield_thread, Filament, Scheduler, Message
# We deliberately do NOT swallow ImportError here: if the C extension is
# missing/unbuilt the package is unusable, and a silent pass would leave an
# empty, confusing namespace.  A clear ImportError is far better.
# ---------------------------------------------------------------------------
try:
    from _filament.core import *  # noqa: F401,F403
    from _filament.core import (  # explicit re-import for names/tools clarity
        spawn as _core_spawn,
        sleep,
        yield_thread,
        Filament,
        Scheduler,
        Message,
    )
except ImportError as _err:  # pragma: no cover
    raise ImportError(
        "filament's C extension (_filament.core) is not available; build it "
        "with 'python setup.py build_ext --inplace'. Original error: %s"
        % (_err,)
    )

# ---------------------------------------------------------------------------
# Pure-Python high-level API.
# ---------------------------------------------------------------------------
from filament import exc  # noqa: E402

from filament.greenthread import (  # noqa: E402
    getcurrent,
    spawn,
    spawn_n,
    spawn_later,
    spawn_after,
    kill,
    killall,
    joinall,
    wait,
    iwait,
    with_timeout,
    GreenThread,
    GreenletExit,
)

from filament.timeout import Timeout  # noqa: E402
from filament.event import Event, AsyncResult  # noqa: E402
from filament.pool import (  # noqa: E402
    Group,
    Pool,
    GreenPool,
    GreenPile,
)
from filament import tpool  # noqa: E402

# Re-export a few C primitives under friendly top-level names so users can do
# filament.Queue / filament.Semaphore / filament.Lock without reaching into the
# _filament.* modules.  These C modules are owned by other agents, so we import
# them defensively -- their absence must not break the core native API above.
try:  # pragma: no cover - availability depends on the compiled extension
    from _filament.locking import Lock, RLock, Condition, Semaphore  # noqa: F401
except ImportError:  # pragma: no cover
    Lock = RLock = Condition = Semaphore = None

try:  # pragma: no cover
    from _filament.queue import Queue, SimpleQueue, Empty, Full  # noqa: F401
except ImportError:  # pragma: no cover
    Queue = SimpleQueue = Empty = Full = None

try:  # pragma: no cover
    from _filament.timer import Timer  # noqa: F401
except ImportError:  # pragma: no cover
    Timer = None


# ---------------------------------------------------------------------------
# Runtime debug mode.
#
# With the vendored greenlet (Python 3 builds), switches skip the eager
# per-switch introspection work (top-frame materialization and, on 3.12+,
# the expose_frames() chain walk) unless debug mode is on.  Reading a parked
# greenthread's ``gr_frame`` always works in either mode: with debug off the
# frame info is reconstructed lazily on access.  Debug mode is also
# auto-armed per switch whenever the switching thread has a trace or profile
# function installed (e.g. ``sys.settrace``), so debuggers see fully
# materialized frames without any explicit toggle.
#
# The FILAMENT_DEBUG environment variable (any value other than empty/"0")
# turns debug mode on at interpreter start.
#
# On classic-greenlet builds (Python 2.7) greenlet is always fully
# eager, so these functions only track the flag and have no behavioral
# effect.
# ---------------------------------------------------------------------------
try:  # vendored greenlet build (Python 3)
    import _fil_greenlet as _fil_greenlet  # noqa: N813
except ImportError:  # pragma: no cover - classic greenlet build (py2.7)
    _fil_greenlet = None

if _fil_greenlet is not None and hasattr(_fil_greenlet, "set_debug"):
    def set_debug(enabled):
        """Enable/disable eager frame introspection on every switch.

        ``set_debug(True)`` additionally sweeps greenthreads that are
        *already* parked (found via ``gc.get_objects()``) and materializes
        their frames immediately, so their ``gr_frame`` state is as if they
        had been parked with debug on.
        """
        enabled = bool(enabled)
        _fil_greenlet.set_debug(enabled)
        if enabled:
            import gc
            greenlet_type = _fil_greenlet.greenlet
            for obj in gc.get_objects():
                if isinstance(obj, greenlet_type):
                    try:
                        # Touching gr_frame materializes a parked
                        # greenlet's frames; harmless otherwise.
                        obj.gr_frame
                    except Exception:  # pragma: no cover - defensive
                        pass

    def get_debug():
        """Return whether eager frame-introspection debug mode is on."""
        return _fil_greenlet.get_debug()
else:  # pragma: no cover - classic greenlet is always eager
    import os as _os
    _classic_debug = [_os.environ.get("FILAMENT_DEBUG", "") not in ("", "0")]

    def set_debug(enabled):
        """No-op flag mirror: classic-greenlet builds are always eager."""
        _classic_debug[0] = bool(enabled)

    def get_debug():
        """Return the (behaviorally inert) debug flag on classic builds."""
        return _classic_debug[0]


__all__ = [
    # C core
    "spawn", "sleep", "yield_thread", "Filament", "Scheduler", "Message",
    # greenthread helpers
    "getcurrent", "spawn_n", "spawn_later", "spawn_after", "kill", "killall",
    "joinall", "wait", "iwait", "with_timeout", "GreenThread", "GreenletExit",
    # timeout / events / pools
    "Timeout", "Event", "AsyncResult",
    "Group", "Pool", "GreenPool", "GreenPile",
    # thread offload
    "tpool",
    # re-exported C primitives
    "Lock", "RLock", "Condition", "Semaphore",
    "Queue", "SimpleQueue", "Empty", "Full", "Timer",
    # runtime debug mode
    "set_debug", "get_debug",
    # exceptions module
    "exc",
]
