import contextlib
import sys

_held_refs = {}

def _is_str(x):
    if sys.version_info >= (3, 0):
        return isinstance(x, str)
    return isinstance(x, basestring)


def _hold_refs(modules):
    # This is called when we've removed something from
    # sys.modules. We want to keep a ref on the module
    # in case something is still using its dict. If
    # the module refcnt goes to 0, the values in its
    # dict are all changed to None. This prevents that
    # from happening.
    if not isinstance(modules, list):
        modules = [ modules ] 
    for m in modules:
        if m is not None:
            _held_refs[id(m)] = m


class ModuleRemover(object):
    __slots__ = ('modules', 'originals', '_skip_restore', '_import')

    def __init__(self, modules, do_import=False, skip_restore=False):
        if _is_str(modules):
            modules = [ modules ]
        if not isinstance(modules, list):
            modules = list(modules)
        self.modules = modules
        self._import = do_import
        self._skip_restore = skip_restore
        self.originals = {}

    def _replace(self):
        for x in self.originals:
            a = sys.modules.pop(x, None)
            if a is not None:
                _hold_refs(a)
            if self.originals[x] is not None:
                sys.modules[x] = self.originals[x]


    def __enter__(self):
        for x in self.modules:
            if x not in self.originals:
                if self._import:
                    __import__(x, level=0)
                self.originals[x] = sys.modules.pop(x, None)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # always restore on exception
        if self._skip_restore and exc_type is None:
            _hold_refs(self.originals.values())
        else:
            self._replace()

    def skip_restore(self):
        self._skip_restore = True


@contextlib.contextmanager
def ModuleReplacer(modulepairs, skip_restore=False):
    with ModuleRemover(map(lambda x: x[0], modulepairs),
            do_import=True,
            skip_restore=skip_restore) as r:
        for (target, obj) in modulepairs:
            if obj is not None:
                sys.modules[target] = obj
        yield r

def copy_globals(source, dest, if_not_exist=True):
    if isinstance(source, dict):
        d = source
    else:
        d = source.__dict__
    for k in iter(d):
        if k not in dest:
            dest[k] = d[k]

def copy_module(mod_name):
    with ModuleRemover(mod_name):
        __import__(mod_name, level=0)
        return sys.modules[mod_name]
