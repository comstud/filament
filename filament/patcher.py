import collections
import sys

from filament import _util

_originals = {}

def get_original(module):
    if module not in _originals:
        __import__(module, level=0)
        _originals[module] = sys.modules[module]
    return _originals[module]

def _get_modules_to_patch(modules):
    module_pairs = []
    for x in modules:
        source = 'filament.' + x
        __import__(source, level=0)
        # __filament__ must exist
        target = sys.modules[source].__filament__.get('patch', x)
        __import__(target)
        if getattr(sys.modules[target], '__filament__', None):
            # already patched
            continue
        module_pairs.append((target, sys.modules[source]))

    return module_pairs

def patch_modules(modules):
    if isinstance(modules, collections.Iterable):
        modules = set(modules)
    else:
        modules = [ modules ]

    modules = _get_modules_to_patch(modules)
    with _util.ModuleReplacer(modules, skip_restore=True) as r:
        pass
    _originals.update(r.originals)

def patch_all(queue=True, socket=True, ssl=True, thread=True, threading=True):
    modules = []

    if queue:
        modules.append('queue')
    if socket:
        modules.append('socket')
    if ssl:
        modules.append('ssl')
    if thread:
        modules.append('thread')
    if threading:
        modules.append('threading')
    patch_modules(modules)
