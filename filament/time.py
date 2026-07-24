"""Cooperative replacement for the ``time`` module.

Only ``time.sleep`` needs to change: the stdlib version blocks the entire OS
thread (and therefore every greenthread sharing it), whereas
``filament.sleep`` yields to the scheduler so other greenthreads run while this
one waits.  Everything else (``time()``, ``monotonic()``, ``strftime()``,
``struct_time``, the clock constants, ...) is copied verbatim from the stdlib.

Importing this module does NOT patch anything globally; call
``filament.patcher.patch_time()`` (or ``patch_all``) for that.
"""

from filament import _util as _fil_util
from filament import patcher as _fil_patcher
import filament as _fil

__filament__ = {'patch': 'time'}

# Pristine stdlib time module.
_orig_time = _fil_patcher.get_original('time')


def sleep(seconds):
    """Cooperatively sleep for ``seconds`` (yields to other greenthreads)."""
    return _fil.sleep(seconds)


# Copy across the rest of the stdlib time module (everything except our
# overridden ``sleep``, which is already defined above).
_fil_util.copy_globals(_orig_time, globals())
