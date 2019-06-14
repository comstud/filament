import sys

def copy_globals(source, dest, if_not_exist=True):
    if isinstance(source, dict):
        d = source
    else:
        d = source.__dict__
    for k in iter(d):
        if k not in dest:
            dest[k] = d[k]

def copy_module(mod_name):
    orig_mod = sys.modules.pop(mod_name, None)
    try:
        return __import__(mod_name, level=0)
    finally:
        if orig_mod is None:
            sys.modules.pop(mod_name, None)
        else:
            sys.modules[mod_name] = orig_mod
