"""Thin re-export of the C timer primitive.

``_filament.timer.Timer(interval, function, *args)`` schedules ``function`` to
run after ``interval`` seconds on the filament scheduler, and can be
``cancel()``-ed before it fires.  This shim exposes it under the ``filament``
namespace; the higher-level, ``threading.Timer``-compatible wrapper lives in
``filament.threading``.
"""

from _filament.timer import *  # noqa: F401,F403
from _filament.timer import Timer  # noqa: F401  (explicit for clarity)
