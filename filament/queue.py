import sys as _sys
from _filament.queue import *

# py2
if _sys.version_info >= (3,0):
    __filament__ = {'patch':'queue'}
else:
    __filament__ = {'patch':'Queue'}
