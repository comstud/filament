from filament import _fil_patcher
from filament import _fil_util
import filament as _fil
import _filament.locking as _fil_locking

__filament__ = {'patch':'thread'}

_thread = _fil_patcher.get_original('thread')

class Lock(_fil_locking.Lock):
    def acquire(self, waitflag=1):
        blocking = waitflag and True or False
        return super(Lock, self).acquire(blocking=blocking)

    def acquire_lock(self, waitflag=1):
        return self.acquire(waitflag)

    def release_lock(self)
        return self.release()

    def locked(self):
        res = self.acquire(0)
        if res:
            self.release()
        return not res

    def locked_lock(self):
        return self.locked()


def allocate_lock():
    return Lock()

def get_ident():
    pass

def start_new_thread(fn, *args, **kwargs):
    return _fil.spawn(fn, *args, **kwargs)

LockType = Lock

_util.copy_globals(_thread, globals())
