# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Tests for filament.select -- the cooperative ``select.select`` replacement.

Covers: integer fds and fileno()-objects, read- and write-readiness, timeout
expiry, mixed ready/unready sets, the (unmonitored) xlist, the ``error``
export, poll()'s NotImplementedError, and an outer ``with Timeout`` firing
while select() is parked in its helper joins.
"""

from __future__ import absolute_import

import os
import sys

import pytest


def _repair_coverage_import_damage():
    # coverage.py resolves dotted --cov targets (e.g. --cov=filament.select)
    # at startup with find_spec() inside its sys_modules_saved() guard.
    # find_spec("filament.X") imports the *parent* package -- the whole
    # filament runtime, including the _filament C extensions, which cache
    # filament.exc.Timeout in C globals -- then the guard strips every newly
    # imported module from sys.modules.  The later real import re-executes the
    # pure-python modules (creating a NEW filament.exc.Timeout class), but
    # CPython hands back the already-initialized C extensions from its
    # extension cache, so the C runtime keeps raising the ORIGINAL Timeout
    # class, which then matches no ``except exc.Timeout`` clause.  Detect that
    # state before importing filament and rebuild filament.exc around the
    # surviving class so there is only one.
    if sys.version_info[0] < 3:
        return                     # py2 has no sysmon coverage core
    if 'filament' in sys.modules or 'filament.exc' in sys.modules:
        return                     # already imported: identity is settled
    import gc
    import importlib.util
    import os.path
    stale = [obj for obj in gc.get_objects()
             if isinstance(obj, type) and obj.__name__ == 'Timeout' and
             getattr(obj, '__module__', None) == 'filament.exc']
    if len(stale) != 1:
        return                     # pristine process (or ambiguous: leave it)
    spec = importlib.util.find_spec('filament')
    if spec is None or not spec.submodule_search_locations:
        return
    path = os.path.join(list(spec.submodule_search_locations)[0], 'exc.py')
    exc_spec = importlib.util.spec_from_file_location('filament.exc', path)
    mod = importlib.util.module_from_spec(exc_spec)
    exc_spec.loader.exec_module(mod)
    mod.Timeout = stale[0]         # re-adopt the class the C runtime holds
    sys.modules['filament.exc'] = mod


_repair_coverage_import_damage()

import filament  # noqa: E402  (must follow the repair guard above)
from filament import select as fil_select  # noqa: E402


class _WithFileno(object):
    """Minimal object exposing the fileno() protocol."""

    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd


def _close_all(*fds):
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass


def test_select_read_ready_int_fd():
    r, w = os.pipe()
    try:
        os.write(w, b'x')
        rl, wl, xl = fil_select.select([r], [], [], 0.2)
        assert rl == [r]
        assert wl == []
        assert xl == []
    finally:
        _close_all(r, w)


def test_select_read_ready_fileno_object():
    r, w = os.pipe()
    try:
        os.write(w, b'x')
        obj = _WithFileno(r)
        rl, wl, xl = fil_select.select([obj], [], [], 0.2)
        # We get back the exact object we passed in, not its fd.
        assert rl == [obj]
        assert wl == []
        assert xl == []
    finally:
        _close_all(r, w)


def test_select_write_ready():
    r, w = os.pipe()
    try:
        # A fresh pipe's write end has buffer space: immediately writable.
        rl, wl, xl = fil_select.select([], [w], [], 0.2)
        assert rl == []
        assert wl == [w]
        assert xl == []
    finally:
        _close_all(r, w)


def test_select_write_ready_fileno_object():
    r, w = os.pipe()
    try:
        obj = _WithFileno(w)
        rl, wl, xl = fil_select.select([], [obj], [], 0.2)
        assert wl == [obj]
    finally:
        _close_all(r, w)


def test_select_timeout_returns_empty():
    r, w = os.pipe()
    try:
        # Nothing ever written: the read end never becomes readable, so the
        # timeout expires and select() reports "nothing ready".
        rl, wl, xl = fil_select.select([r], [], [], 0.05)
        assert (rl, wl, xl) == ([], [], [])
    finally:
        _close_all(r, w)


def test_select_write_timeout_returns_empty():
    r, w = os.pipe()
    try:
        # Fill the pipe buffer so the write end is NOT writable.
        # (fcntl rather than os.set_blocking: the suite stays py2.7-clean.)
        import fcntl
        flags = fcntl.fcntl(w, fcntl.F_GETFL)
        fcntl.fcntl(w, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        try:
            while True:
                os.write(w, b'x' * 65536)
        except OSError:
            pass
        rl, wl, xl = fil_select.select([], [w], [], 0.05)
        assert (rl, wl, xl) == ([], [], [])
    finally:
        _close_all(r, w)


def test_select_mixed_ready_and_unready():
    r1, w1 = os.pipe()
    r2, w2 = os.pipe()
    try:
        os.write(w1, b'x')       # r1 readable; r2 never becomes readable
        rl, wl, xl = fil_select.select([r1, r2], [w2], [], 0.1)
        assert rl == [r1]
        assert wl == [w2]
        assert xl == []
    finally:
        _close_all(r1, w1, r2, w2)


def test_select_xlist_accepted_but_always_empty():
    r, w = os.pipe()
    try:
        os.write(w, b'x')
        rl, wl, xl = fil_select.select([r], [], [r, w], 0.1)
        assert rl == [r]
        # xlist is accepted but never monitored -- always comes back empty.
        assert xl == []
    finally:
        _close_all(r, w)


def test_select_outer_timeout_propagates():
    # An outer ``with Timeout`` firing while select() is parked joining its
    # helper greenthreads must propagate (it is not select()'s own timeout).
    r, w = os.pipe()
    try:
        def run():
            with filament.Timeout(0.05):
                fil_select.select([r], [], [], 0.2)

        with pytest.raises(filament.Timeout):
            filament.spawn(run).wait()
        # Let the helper greenthreads finish their own (0.2s) waits before we
        # close the descriptors out from under the IO thread.
        filament.sleep(0.25)
    finally:
        _close_all(r, w)


def test_select_error_export():
    # We re-export whatever the stdlib has (OSError on py3, its own class on
    # py2).
    import select as std_select
    assert fil_select.error is getattr(std_select, 'error', OSError)


def test_poll_raises_notimplemented():
    with pytest.raises(NotImplementedError):
        fil_select.poll()


def test_stdlib_constants_copied():
    # copy_globals pulls stdlib names we do not override (Linux has POLLIN).
    assert hasattr(fil_select, 'POLLIN')
