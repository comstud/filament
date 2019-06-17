from filament import patcher
from filament import _util
from _filament.locking import *
import _filament.timer as _fil_timer

__filament__ = {'patch':'threading'}

threading = patcher.get_original('threading')

class Timer(_fil_timer.Timer):
    __doc__ = threading.Timer.__doc__

    def __init__(self, interval, function, args=None, kwargs=None):
        if args is None:
            args = ()
        if kwargs:
            super(Timer, self).__init__(interval, function, *args, **kwargs)
        else:
            super(Timer, self).__init__(interval, function, *args)


_util.copy_globals(threading, globals())
