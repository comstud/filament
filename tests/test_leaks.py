# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Regression tests for the greenthread leaks and the shutdown hangs.

Four distinct bugs are covered here; each of these tests fails on the code
immediately before the fix, so please do not weaken them without checking that
they still catch the original bug:

1. ``Filament`` was GC-tracked but inherited greenlet's ``tp_traverse``, which
   does not know about ``method``/``method_args``/``method_kwargs``.  The
   ``wrapper -> wrapper._filament -> Filament.method -> bound method ->
   wrapper`` cycle every compat shim creates was therefore *invisible* to the
   collector, not merely uncollected.
2. ``Message`` had the same problem for the value/exception it stores, which
   closes the same cycle through a traceback's frame.
3. ``_fil_filament_dealloc`` called ``tp_free`` directly instead of chaining to
   greenlet's ``tp_dealloc``, leaking greenlet's C++ ``pimpl`` (one
   ``PyObject_Malloc`` block per Filament) with no Python object to show
   for it.
4. ``_thrpool_dealloc`` ran a blocking shutdown at interpreter finalization,
   where the helper thread can never re-acquire the GIL -- an unconditional
   hang at exit whenever stdout was a pipe.

Written to run on Python 2.7 as well as 3.x.
"""

from __future__ import absolute_import

import gc
import sys

import pytest

from tests._helpers import run_py

import filament


def _filament_type():
    import _filament.core
    return _filament.core.Filament


def _count_filaments():
    filament_type = _filament_type()
    return sum(1 for o in gc.get_objects() if type(o) is filament_type)


def _collect():
    # Two passes: the first can resurrect nothing here, but it does free
    # objects whose release makes a second cycle unreachable.
    gc.collect()
    gc.collect()


class _Wrapper(object):
    """The shape every compat shim uses: bound-method body + back-reference."""

    def __init__(self):
        self.value = None

    def body(self):
        self.value = 42


def _spawn_wrapped():
    wrapper = _Wrapper()
    fil = filament.spawn(wrapper.body)
    wrapper._filament = fil          # closes the cycle
    fil.wait()


# -- 1. traverse actually reports what we hold -------------------------------

def test_filament_traverse_reports_its_callable():
    def target():
        pass

    fil = filament.spawn(target)
    referents = gc.get_referents(fil)
    assert target in referents, \
        "Filament.tp_traverse must report the callable it holds, else the " \
        "collector cannot see wrapper cycles"
    fil.wait()


def test_filament_releases_callable_once_body_has_run():
    def target():
        pass

    fil = filament.spawn(target)
    fil.wait()
    # The body can never run again, so the callable must be dropped eagerly --
    # that is what breaks the wrapper cycle by refcount rather than by gc.
    assert target not in gc.get_referents(fil)


def test_message_traverse_reports_its_result():
    from _filament.core import Message

    sentinel = ["result"]
    msg = Message()
    msg.send(sentinel)
    assert sentinel in gc.get_referents(msg)


# -- 2. the wrapper cycle is actually reclaimed ------------------------------

def test_bound_method_wrapper_cycle_does_not_leak():
    for _ in range(50):
        _spawn_wrapped()
    _collect()
    before = _count_filaments()

    for _ in range(200):
        _spawn_wrapped()
    _collect()
    after = _count_filaments()

    assert after - before <= 5, \
        "leaked %d Filaments over 200 spawns" % (after - before,)


def test_gevent_compat_spawn_does_not_leak():
    _check_shim_does_not_leak('''
import gc
import filament.gevent_compat as gcompat
gcompat.install()
import gevent
import _filament.core

def work():
    pass

def count():
    return sum(1 for o in gc.get_objects()
               if type(o) is _filament.core.Filament)
''', "gevent.spawn(work).join()")


def test_eventlet_compat_spawn_does_not_leak():
    _check_shim_does_not_leak('''
import gc
import filament.eventlet_compat as ecompat
ecompat.install()
import eventlet
import _filament.core

def work():
    pass

def count():
    return sum(1 for o in gc.get_objects()
               if type(o) is _filament.core.Filament)
''', "eventlet.spawn(work).wait()")


def _check_shim_does_not_leak(preamble, spawn_expr):
    # In a subprocess: installing a compat shim rewrites sys.modules, which we
    # must not do to the test process.
    res = run_py(preamble + '''
for _ in range(50):
    %s
gc.collect(); gc.collect()
before = count()

for _ in range(500):
    %s
gc.collect(); gc.collect()
after = count()

assert after - before <= 5, "leaked %%d Filaments" %% (after - before,)
print("OK")
''' % (spawn_expr, spawn_expr))
    assert not res.timed_out, repr(res)
    assert res.returncode == 0, repr(res)
    assert "OK" in res.stdout, repr(res)


def test_killed_greenthread_does_not_leak():
    # A killed greenthread delivers GreenletExit through its Message, whose
    # traceback holds the frame of the body -- i.e. the wrapper -- closing the
    # cycle through the Message rather than through Filament.method.
    def spawn_and_kill():
        wrapper = _Wrapper()
        event = filament.Event()
        wrapper.event = event
        wrapper._filament = filament.spawn(lambda: event.wait())
        filament.kill(wrapper._filament)

    for _ in range(50):
        spawn_and_kill()
    _collect()
    before = _count_filaments()

    for _ in range(200):
        spawn_and_kill()
    _collect()
    after = _count_filaments()

    assert after - before <= 5, \
        "leaked %d Filaments over 200 kills" % (after - before,)


# -- 3. the greenlet pimpl leak (no Python object to count) ------------------

@pytest.mark.skipif(not hasattr(sys, "getallocatedblocks"),
                    reason="sys.getallocatedblocks() is Python 3 only")
def test_spawning_does_not_leak_raw_allocator_blocks():
    """
    greenlet's C++ ``pimpl`` is allocated with ``PyObject_Malloc``, so it shows
    up in ``sys.getallocatedblocks()`` but in no object graph.  Before the
    dealloc chaining fix this leaked exactly one block per Filament.
    """
    def target():
        pass

    def batch(n):
        for _ in range(n):
            filament.spawn(target).wait()

    batch(500)              # warm every freelist first
    _collect()
    before = sys.getallocatedblocks()

    batch(2000)
    _collect()
    after = sys.getallocatedblocks()

    leaked_per_spawn = (after - before) / 2000.0
    assert leaked_per_spawn < 0.2, \
        "leaked %.3f allocator blocks per spawn" % (leaked_per_spawn,)


# -- 4. clean interpreter shutdown -------------------------------------------

_SHUTDOWN_CASES = [
    # A bare C thread pool that is never explicitly shut down.
    ('''
from _filament.thrpool import ThreadPool
tp = ThreadPool()
print("OK")
'''),
    # ... and one that is still referenced when finalization starts.
    ('''
import sys
from _filament.thrpool import ThreadPool
sys._filament_keepalive = ThreadPool()
print("OK")
'''),
    # The Python-level default pool, used and then abandoned.
    ('''
import filament, filament.tpool
assert filament.tpool.execute(lambda: 1 + 1) == 2
print("OK")
'''),
    # A greenthread still parked when the interpreter exits.
    ('''
import filament
filament.spawn(filament.sleep, 3600)
print("OK")
'''),
]


@pytest.mark.parametrize("script", _SHUTDOWN_CASES)
def test_interpreter_exits_cleanly(script):
    """
    ``run_py`` gives the child a *pipe* for stdout, which matters: the
    finalization hang this guards against did not reproduce on a tty.
    """
    res = run_py(script, timeout=25)
    assert not res.timed_out, "interpreter hung at shutdown\n" + repr(res)
    assert res.returncode == 0, repr(res)
    assert "OK" in res.stdout, repr(res)


def test_filament_submodule_is_importable_first():
    # _filament.core's init imports filament.exc, which re-enters us through
    # filament.greenthread -> _filament.timer.  Importing any _filament
    # submodule before the filament package used to fail on Python 2.
    res = run_py('''
from _filament.thrpool import ThreadPool
import filament
assert filament.spawn(lambda: 7).wait() == 7
print("OK")
''')
    assert not res.timed_out, repr(res)
    assert res.returncode == 0, repr(res)
    assert "OK" in res.stdout, repr(res)


# -- 5. Group/Pool must not accumulate finished greenthreads -----------------

def test_group_untracks_finished_greenthreads():
    from filament.pool import Group

    group = Group()
    for _ in range(100):
        group.spawn(lambda: None).wait()
    filament.sleep(0)
    assert len(group.greenlets) == 0, \
        "Group kept %d finished greenthreads" % (len(group.greenlets),)


def test_pool_untracks_finished_greenthreads():
    from filament.pool import Pool

    pool = Pool(size=4)
    for _ in range(100):
        pool.spawn(lambda: None).wait()
    filament.sleep(0)
    assert len(pool.greenlets) == 0, \
        "Pool kept %d finished greenthreads" % (len(pool.greenlets),)


def test_group_join_still_waits_for_live_greenthreads():
    from filament.pool import Group

    done = []
    group = Group()
    for _ in range(5):
        group.spawn(lambda: (filament.sleep(0.01), done.append(1)))
    assert len(group.greenlets) == 5
    group.join()
    assert done == [1] * 5
    assert len(group.greenlets) == 0
