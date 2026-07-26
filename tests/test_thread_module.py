# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Tests for ``filament.thread`` (the green low-level thread module) and the
``filament.timer`` re-export shim:

  * ``LockType``: ``acquire(waitflag)`` / timeout variants and the historic
    ``acquire_lock``/``release_lock``/``locked``/``locked_lock`` aliases.
  * ``allocate_lock``/``allocate`` factories.
  * ``get_ident``: a unique non-zero int per live greenthread.
  * ``start_new_thread``/``start_new``: runs the function with args/kwargs in
    a filament and returns an integer identity.
  * ``filament.timer`` re-exports ``_filament.timer.Timer``; Timer fires with
    args after its interval and ``cancel()`` disarms it.
"""

from __future__ import absolute_import

import filament
from filament import thread as fil_thread
from filament import timer as fil_timer

import _filament.locking as _fil_locking
import _filament.timer as _c_timer


def run(fn):
    return filament.spawn(fn).wait()


def _make_lock():
    # NB: LockType is a Python subclass of the C ``_filament.locking.Lock``.
    # The C deallocs used to free heap-subclass instances with a bare
    # PyObject_Del (heap corruption; segfault at finalization after two
    # deallocs) -- fixed to respect tp_free, so plain allocation is safe now.
    return fil_thread.allocate_lock()


# --------------------------------------------------------------------------- #
# LockType
# --------------------------------------------------------------------------- #

def test_allocate_lock_and_aliases():
    lock = _make_lock()
    assert isinstance(lock, fil_thread.LockType)
    assert isinstance(lock, _fil_locking.Lock)
    assert fil_thread.allocate is fil_thread.allocate_lock


def test_lock_acquire_release_and_locked():
    lock = _make_lock()
    assert lock.locked() is False
    assert lock.acquire() is True          # default waitflag=1, timeout=-1
    assert lock.locked() is True
    assert lock.locked_lock() is True
    lock.release()
    assert lock.locked() is False
    assert lock.locked_lock() is False


def test_lock_nonblocking_acquire():
    lock = _make_lock()
    assert lock.acquire(0) is True         # free: non-blocking acquire wins
    assert lock.acquire(0) is False        # held: non-blocking acquire fails
    lock.release()


def test_lock_historic_acquire_release_aliases():
    lock = _make_lock()
    assert lock.acquire_lock() is True
    assert lock.acquire_lock(0) is False   # already held
    lock.release_lock()
    assert lock.locked() is False


def test_lock_acquire_timeout_variants():
    # NOTE: the expired-timeout path (acquire a *held* lock with a timeout) is
    # NOT exercised here: the C lock raises SystemError when the timeout
    # expires -- a known library bug already pinned by xfail tests in
    # test_locking.py.  The timeout *argument* plumbing is covered via a free
    # lock, where the timed acquire succeeds immediately.
    def body():
        lock = _make_lock()
        # timeout=None means "no timeout": plain blocking acquire.
        assert lock.acquire(1, None) is True
        lock.release()
        # A real timeout routes through acquire(blocking=..., timeout=...).
        assert lock.acquire(1, timeout=0.5) is True
        lock.release()
        return True

    assert run(body) is True


# --------------------------------------------------------------------------- #
# get_ident
# --------------------------------------------------------------------------- #

def test_get_ident_unique_per_greenthread():
    def body():
        ev = filament.Event()
        idents = []

        def child():
            idents.append(fil_thread.get_ident())
            ev.wait()                       # stay alive so ids can't recycle

        g1 = filament.spawn(child)
        g2 = filament.spawn(child)
        for _ in range(500):
            if len(idents) == 2:
                break
            filament.sleep(0.005)
        main_ident = fil_thread.get_ident()
        ev.set()
        g1.wait()
        g2.wait()
        return idents, main_ident

    idents, main_ident = run(body)
    assert len(idents) == 2
    all_ids = idents + [main_ident]
    assert len(set(all_ids)) == 3           # three live filaments, three ids
    for ident in all_ids:
        assert isinstance(ident, int)
        assert ident != 0


# --------------------------------------------------------------------------- #
# start_new_thread / start_new
# --------------------------------------------------------------------------- #

def test_start_new_thread_runs_with_args_and_kwargs():
    def body():
        out = []
        ev = filament.Event()

        def work(a, b, c=None):
            out.append((a, b, c))
            ev.set()

        ident = fil_thread.start_new_thread(work, (1, 2), {"c": 3})
        ev.wait()
        return out, ident

    out, ident = run(body)
    assert out == [(1, 2, 3)]
    assert isinstance(ident, int)
    assert ident != 0


def test_start_new_thread_default_kwargs():
    def body():
        out = []
        ev = filament.Event()

        def work(a):
            out.append(a)
            ev.set()

        fil_thread.start_new_thread(work, ("solo",))
        ev.wait()
        return out

    assert run(body) == ["solo"]


def test_start_new_is_alias():
    assert fil_thread.start_new is fil_thread.start_new_thread


def test_unoverridden_names_copied_from_original():
    # copy_globals brings across everything we didn't green (error, etc.).
    assert hasattr(fil_thread, "error")


# --------------------------------------------------------------------------- #
# filament.timer re-export + Timer behavior
# --------------------------------------------------------------------------- #

def test_timer_module_reexports_c_timer():
    assert fil_timer.Timer is _c_timer.Timer


def test_timer_fires_with_args():
    def body():
        out = []
        fil_timer.Timer(0.01, lambda a, b: out.append((a, b)), "x", 7)
        assert out == []
        filament.sleep(0.05)
        return out

    assert run(body) == [("x", 7)]


def test_timer_cancel_prevents_fire():
    def body():
        out = []
        t = fil_timer.Timer(0.05, lambda: out.append("nope"))
        t.cancel()
        filament.sleep(0.1)
        return out

    assert run(body) == []
