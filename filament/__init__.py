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
    # exceptions module
    "exc",
]
