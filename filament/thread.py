from filament import patcher
from filament import _util
from _filament.locking import *

__filament__ = {'patch':'thread'}

thread = patcher.get_original('thread')



_util.copy_globals(thread, globals())
