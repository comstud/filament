from filament import patcher as _fil_patcher
from filament import _util as _fil_util
import _filament.timer as _fil_timer
from _filament.locking import *

__filament__ = {'patch':'threading'}

threading = _fil_patcher.get_original('threading')

class Timer(_fil_timer.Timer):
    __doc__ = threading.Timer.__doc__

    def __init__(self, interval, function, args=None, kwargs=None):
        if args is None:
            args = ()
        if kwargs:
            super(Timer, self).__init__(interval, function, *args, **kwargs)
        else:
            super(Timer, self).__init__(interval, function, *args)


_fil_util.copy_globals(threading, globals())
