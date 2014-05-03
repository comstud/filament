try:
    from filament._cext import *
except ImportError:
    # No C extension available
    # FIXME(comstud): Create python versions of stuff
    from filament import patcher

#from filament import sched
#from filament import lock
#from filament import cond
#from filament import semaphore


#spawn = filament.core.spawn
#Semaphore = filament.core.Semaphore
#yield_thread = filament.core.yield_thread
