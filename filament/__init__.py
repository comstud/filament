try:
    from _filament.core import *  # noqa
except ImportError:
    # No C extension available
    # FIXME(comstud): Create python versions of stuff
    pass
