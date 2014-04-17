import sys

from filament.eventlet_compat import greenthread
from filament.eventlet_compat import main

_MODULE_MAP = {'eventlet': main,
               'eventlet.greenthread': greenthread}

def install():
    for name, mod in _MODULE_MAP.items():
        sys.modules[name] = mod
