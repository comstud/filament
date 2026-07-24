"""Thin re-export of the C thread-pool primitive.

``_filament.thrpool.ThreadPool`` runs blocking callables on real OS worker
threads while the calling greenthread waits cooperatively -- used e.g. by the
DNS resolver (``filament.thrpool_resolver``).  This shim just exposes it under
the ``filament`` namespace; there is nothing to green here.
"""

from _filament.thrpool import *  # noqa: F401,F403
from _filament.thrpool import ThreadPool  # noqa: F401  (explicit for clarity)
