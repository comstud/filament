# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Coverage-gap tests for filament.patcher.

Anything that mutates real global patch state (patch_all / patch_modules /
_patch_module) runs in a fresh subprocess via run_py, following the pattern in
test_patcher.py.  The pure helpers (item patching on a throwaway module,
get_original lookups, the logging-handler iterator, the lock sweep) are safe to
exercise in-process.
"""

from __future__ import absolute_import

import sys
import types
import weakref

from filament import patcher as fil_patcher

from tests._helpers import run_py


# A unique throwaway module name so in-process item patching cannot collide
# with anything real.
_DUMMY = 'fil_test_patcher_dummy'


def _make_dummy():
    mod = types.ModuleType(_DUMMY)
    mod.x = 'orig-x'
    mod.y = 'orig-y'
    sys.modules[_DUMMY] = mod
    return mod


def _drop_dummy():
    sys.modules.pop(_DUMMY, None)
    fil_patcher.saved.pop(_DUMMY, None)
    fil_patcher._originals.pop(_DUMMY, None)


def test_patch_item_by_name_and_introspection():
    _make_dummy()
    try:
        # String module argument exercises the _import branch.
        fil_patcher.patch_item(_DUMMY, 'x', 'green-x')
        assert sys.modules[_DUMMY].x == 'green-x'
        assert fil_patcher.is_object_patched(_DUMMY, 'x') is True
        assert fil_patcher.is_object_patched(_DUMMY, 'y') is False
        assert fil_patcher.is_object_patched('no_such_mod', 'x') is False
        # Idempotent: repatching keeps the first saved original.
        fil_patcher.patch_item(_DUMMY, 'x', 'greener-x')
        assert fil_patcher.get_original(_DUMMY, 'x') == 'orig-x'
        # Un-patched item resolves live; list form returns the originals.
        assert fil_patcher.get_original(_DUMMY, 'y') == 'orig-y'
        assert fil_patcher.get_original(_DUMMY, ['x', 'y']) == \
            ['orig-x', 'orig-y']
    finally:
        _drop_dummy()


def test_iter_logging_handlers_dedup_and_fallbacks():
    class Handler(object):
        pass

    h1 = Handler()
    h2 = Handler()
    h3 = Handler()

    # _handlerList holds weakrefs (Py3 style) or bare handlers (Py2 style);
    # _handlers may be a dict or, on odd versions, a plain sequence.
    fake = types.SimpleNamespace(
        _handlerList=[weakref.ref(h1), h2],
        _handlers={'h2': h2, 'h3': h3},
    )
    got = list(fil_patcher._iter_logging_handlers(fake))
    assert sorted(id(h) for h in got) == sorted([id(h1), id(h2), id(h3)])

    # Sequence-shaped _handlers takes the AttributeError fallback.
    fake2 = types.SimpleNamespace(_handlerList=[], _handlers=[h1])
    assert list(fil_patcher._iter_logging_handlers(fake2)) == [h1]

    # A dead weakref and an empty _handlers yield nothing.
    fake3 = types.SimpleNamespace(_handlerList=[weakref.ref(Handler())],
                                  _handlers=None)
    assert list(fil_patcher._iter_logging_handlers(fake3)) == []


def test_sweep_existing_locks_early_returns(monkeypatch):
    sweep = fil_patcher._sweep_existing_python_locks

    # No threading module at all.
    monkeypatch.delitem(sys.modules, 'threading')
    assert sweep() is None
    monkeypatch.undo()

    # threading without _PyRLock.
    monkeypatch.setitem(sys.modules, 'threading',
                        types.SimpleNamespace())
    assert sweep() is None
    monkeypatch.undo()

    # _PyRLock present but no RLock.
    monkeypatch.setitem(
        sys.modules, 'threading',
        types.SimpleNamespace(_PyRLock=type('PyR', (), {}), RLock=None))
    assert sweep() is None
    monkeypatch.undo()

    # RLock that is not a reassignable class.
    monkeypatch.setitem(
        sys.modules, 'threading',
        types.SimpleNamespace(_PyRLock=type('PyR', (), {}),
                              RLock=lambda: None))
    assert sweep() is None


def test_sweep_existing_locks_swaps_and_guards(monkeypatch):
    class PyR(object):
        pass

    class GreenR(PyR):
        pass

    class SlottedGreen(object):
        __slots__ = ('a',)

    swappable = PyR()
    monkeypatch.setitem(
        sys.modules, 'threading',
        types.SimpleNamespace(_PyRLock=PyR, RLock=GreenR))
    fil_patcher._sweep_existing_python_locks()
    assert type(swappable) is GreenR
    monkeypatch.undo()

    # Layout-incompatible target: the TypeError is swallowed, instance intact.
    stuck = PyR()
    monkeypatch.setitem(
        sys.modules, 'threading',
        types.SimpleNamespace(_PyRLock=PyR, RLock=SlottedGreen))
    fil_patcher._sweep_existing_python_locks()
    assert type(stuck) is PyR


def test_patch_all_dns_only_subprocess():
    res = run_py('''
import socket
orig_getaddrinfo = socket.getaddrinfo
from filament import patcher
patcher.patch_all(socket=False, dns=True, ssl=False, subprocess=False)
import sys
assert sys.modules["socket"] is not None
assert patcher.is_module_patched("socket") is False
assert patcher.is_object_patched("socket", "getaddrinfo") is True
assert socket.getaddrinfo is not orig_getaddrinfo
assert patcher.get_original("socket", "getaddrinfo") is orig_getaddrinfo
print("OK")
''')
    assert res.ok(), repr(res)
    assert 'OK' in res.stdout


def test_patch_modules_string_list_and_idempotence_subprocess():
    res = run_py('''
import sys
import time as orig_time
from filament import patcher
patcher.patch_modules("time")
import filament.time
assert sys.modules["time"] is filament.time
assert patcher.is_module_patched("time") is True
# List form; "time" is already patched and must be skipped, "select" patched.
patcher.patch_modules(["select", "time"])
import filament.select
assert sys.modules["select"] is filament.select
assert patcher.get_original("time") is orig_time
print("OK")
''')
    assert res.ok(), repr(res)
    assert 'OK' in res.stdout


def test_patch_module_missing_stdlib_target_subprocess():
    # A green module whose declared target does not exist on this platform:
    # the ImportError path records None as the original and still installs.
    res = run_py('''
import sys, types
from filament import patcher
green = types.ModuleType("fil_green_fake")
patcher._patch_module("fil_no_such_stdlib_mod", green)
assert sys.modules["fil_no_such_stdlib_mod"] is green
assert patcher.is_module_patched("fil_no_such_stdlib_mod") is True
# The recorded original is None, which get_original cannot distinguish from
# "never patched" -- it falls back to sys.modules and hands back the green
# module (the only live object under that name).
assert patcher.get_original("fil_no_such_stdlib_mod") is green
print("OK")
''')
    assert res.ok(), repr(res)
    assert 'OK' in res.stdout
