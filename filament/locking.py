"""Thin re-export of the C cooperative locking primitives.

``_filament.locking`` provides ``Lock``, ``RLock``, ``Condition`` and
``Semaphore`` that block *cooperatively*: a greenthread that cannot acquire a
lock yields to the scheduler instead of blocking the OS thread.  This shim just
surfaces them under the ``filament`` namespace; the ``threading``-compatible
wrappers (thread-style ``acquire(waitflag)`` etc.) live in ``filament.thread``
and ``filament.threading``.
"""

from _filament.locking import *  # noqa: F401,F403
from _filament.locking import (  # noqa: F401  (explicit for clarity)
    Lock,
    RLock,
    Condition,
    Semaphore,
)
