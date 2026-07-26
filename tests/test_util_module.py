# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Tests for filament._util (module-swapping helpers) and
filament._threading_local (per-greenthread ``local`` storage).

_util: ModuleRemover's skip_restore flag + method, restore-on-exception, and
copy_globals with a plain-dict source.

_threading_local: TypeError on args without a custom __init__, per-greenthread
__init__ re-runs, attribute isolation across greenthreads, __delattr__, and
the read-only __dict__ guards.
"""

from __future__ import absolute_import

import sys
import types
import weakref

import pytest


def _repair_coverage_import_damage():
    # See tests/test_select_module.py for the full story: coverage.py's dotted
    # --cov targets import-and-discard the filament package at startup, leaving
    # the _filament C extensions holding a stale filament.exc.Timeout class.
    # Rebuild filament.exc around that class before importing filament.
    if sys.version_info[0] < 3:
        return
    if 'filament' in sys.modules or 'filament.exc' in sys.modules:
        return
    import gc
    import importlib.util
    import os.path
    stale = [obj for obj in gc.get_objects()
             if isinstance(obj, type) and obj.__name__ == 'Timeout' and
             getattr(obj, '__module__', None) == 'filament.exc']
    if len(stale) != 1:
        return
    spec = importlib.util.find_spec('filament')
    if spec is None or not spec.submodule_search_locations:
        return
    path = os.path.join(list(spec.submodule_search_locations)[0], 'exc.py')
    exc_spec = importlib.util.spec_from_file_location('filament.exc', path)
    mod = importlib.util.module_from_spec(exc_spec)
    exc_spec.loader.exec_module(mod)
    mod.Timeout = stale[0]
    sys.modules['filament.exc'] = mod


_repair_coverage_import_damage()

import filament  # noqa: E402  (must follow the repair guard above)
from filament import _threading_local as fil_local  # noqa: E402
from filament import _util as fil_util  # noqa: E402


_DUMMY_NAME = 'fil_test_util_dummy_mod'


def _install_dummy():
    mod = types.ModuleType(_DUMMY_NAME)
    mod.marker = 'dummy'
    sys.modules[_DUMMY_NAME] = mod
    return mod


# --------------------------------------------------------------------------- #
# _util.ModuleRemover / copy_globals
# --------------------------------------------------------------------------- #

def test_module_remover_removes_and_restores():
    mod = _install_dummy()
    try:
        with fil_util.ModuleRemover(_DUMMY_NAME):
            assert _DUMMY_NAME not in sys.modules
        assert sys.modules.get(_DUMMY_NAME) is mod
    finally:
        sys.modules.pop(_DUMMY_NAME, None)


def test_module_remover_skip_restore_flag():
    _install_dummy()
    try:
        with fil_util.ModuleRemover(_DUMMY_NAME, skip_restore=True):
            assert _DUMMY_NAME not in sys.modules
        # skip_restore on a clean exit: the module stays removed.
        assert _DUMMY_NAME not in sys.modules
    finally:
        sys.modules.pop(_DUMMY_NAME, None)


def test_module_remover_skip_restore_method():
    _install_dummy()
    try:
        with fil_util.ModuleRemover(_DUMMY_NAME) as remover:
            remover.skip_restore()
        assert _DUMMY_NAME not in sys.modules
    finally:
        sys.modules.pop(_DUMMY_NAME, None)


def test_module_remover_restores_on_exception_despite_skip_restore():
    mod = _install_dummy()
    try:
        with pytest.raises(RuntimeError):
            with fil_util.ModuleRemover(_DUMMY_NAME, skip_restore=True):
                assert _DUMMY_NAME not in sys.modules
                raise RuntimeError('boom')
        # An exception always restores, even with skip_restore set.
        assert sys.modules.get(_DUMMY_NAME) is mod
    finally:
        sys.modules.pop(_DUMMY_NAME, None)


def test_copy_globals_from_dict_source():
    dest = {'a': 1}
    fil_util.copy_globals({'a': 2, 'b': 3}, dest)
    # Only missing names are filled in; existing ones are never clobbered.
    assert dest == {'a': 1, 'b': 3}


def test_copy_globals_from_module_source():
    mod = types.ModuleType('fil_test_copy_globals_src')
    mod.x = 'from-module'
    dest = {}
    fil_util.copy_globals(mod, dest)
    assert dest['x'] == 'from-module'


# --------------------------------------------------------------------------- #
# _threading_local.local
# --------------------------------------------------------------------------- #

def test_local_args_without_custom_init_raises():
    with pytest.raises(TypeError):
        fil_local.local(1)
    with pytest.raises(TypeError):
        fil_local.local(x=1)


def test_local_basic_attribute_roundtrip():
    loc = fil_local.local()
    loc.value = 'main'
    assert loc.value == 'main'


def test_local_isolation_across_greenthreads():
    loc = fil_local.local()
    loc.value = 'main'
    seen = []

    def worker(tag):
        # A fresh greenthread starts with an empty attribute dict.
        seen.append(hasattr(loc, 'value'))
        loc.value = tag
        filament.sleep(0)          # let the other worker interleave
        seen.append(loc.value)

    g1 = filament.spawn(worker, 'one')
    g2 = filament.spawn(worker, 'two')
    g1.join()
    g2.join()
    assert seen[0] is False and seen[1] is False
    assert sorted(seen[2:]) == ['one', 'two']
    assert loc.value == 'main'     # main greenthread's value untouched


def test_local_subclass_init_reruns_per_greenthread():
    inits = []

    class MyLocal(fil_local.local):
        def __init__(self, base):
            inits.append(base)
            self.base = base

    loc = MyLocal(10)
    assert loc.base == 10
    main_inits = len(inits)
    assert main_inits >= 1

    got = []

    def worker():
        got.append(loc.base)       # first touch re-runs __init__(10) here
        loc.base = 99
        got.append(loc.base)

    filament.spawn(worker).join()
    assert got == [10, 99]
    assert len(inits) > main_inits  # __init__ ran again in the worker
    assert loc.base == 10           # worker's overwrite did not leak to main


def test_local_delattr():
    loc = fil_local.local()
    loc.value = 1
    del loc.value
    assert not hasattr(loc, 'value')
    with pytest.raises(AttributeError):
        del loc.never_set


def test_local_dict_is_readonly():
    loc = fil_local.local()
    with pytest.raises(AttributeError):
        loc.__dict__ = {}
    with pytest.raises(AttributeError):
        del loc.__dict__


def test_local_private_bookkeeping_accessible():
    loc = fil_local.local()
    loc.value = 'x'
    # The name-mangled bookkeeping slots resolve through __getattribute__'s
    # special-case branch.
    assert isinstance(loc._local__dicts, weakref.WeakKeyDictionary)
    args, kwargs = loc._local__args
    assert args == () and kwargs == {}
