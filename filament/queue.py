"""Green ``queue`` module (``Queue`` on Python 2).

This exposes filament's cooperative C queue implementation
(``_filament.queue``: ``Queue``, ``SimpleQueue``, ``Empty``, ``Full``) under the
stdlib queue module name so patched code gets queues whose blocking
``get``/``put`` calls yield to the scheduler.  A pure-Python fallback lives in
``filament.pyqueue``.

The ``__filament__`` marker tells the patcher which stdlib module to stand in
for -- and that name differs between Python versions: it is ``queue`` on
Python 3 and ``Queue`` on Python 2.
"""

import sys as _sys

from _filament.queue import *  # noqa: F401,F403
from _filament.queue import Empty, Full, Queue, SimpleQueue  # noqa: F401

if _sys.version_info[0] >= 3:
    __filament__ = {'patch': 'queue'}
else:  # pragma: no cover - Python 2
    __filament__ = {'patch': 'Queue'}
