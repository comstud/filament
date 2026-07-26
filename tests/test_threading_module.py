# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Coverage-gap tests for ``filament.threading``:

  * ``BoundedSemaphore``: normal acquire/release plus the over-release
    ``ValueError`` -- exercised in a fresh subprocess (see the second NOTE
    below for why).
  * ``_CondEvent`` (the Condition-based Event fallback, exercised directly):
    set/clear/is_set/isSet, immediate wait, cross-greenthread wakeup, and the
    timeout path.
  * ``Thread``: constructor validation, double-start / early-join errors,
    lifecycle (``is_alive``), name/ident/daemon accessors (properties and the
    deprecated get*/set* forms), ``current_thread`` from inside and outside a
    cooperative Thread, ``active_count`` and ``enumerate``.
  * ``Timer``: the cancel path, and the (racy but real) set-then-clear path
    that makes the callback fire early.

NOTE ON A LIBRARY QUIRK (now handled in filament.threading):
``_filament.locking.Condition.wait(timeout=...)`` raises
``filament.exc.Timeout``
when the timeout expires instead of returning (the stdlib Condition returns
False).  ``_CondEvent.wait`` catches that and returns the flag state, which
restores the stdlib contracts for ``Event.wait(timeout)`` (returns False),
``Thread.join(timeout)`` (returns quietly) and ``Timer`` (callback fires after
the interval); the tests below pin those contracts.
An identity trap remains: when running under coverage, the
``Timeout`` instance the C extension raises is built from a *different copy* of
the ``filament.exc.Timeout`` class than the one ``from filament import exc``
yields (the extension holds a class object from an earlier import that no
longer matches ``sys.modules['filament.exc']``), so identity-based catches like
``pytest.raises(exc.Timeout)`` miss it and the exception escapes the
greenthread (the pre-existing ``test_locking.py::test_condition_wait_timeout_
raises`` fails under coverage for the same reason).  The timeout-quirk tests
below therefore match the exception by module/name instead of by class
identity.

NOTE ON A SECOND LIBRARY BUG (since fixed in the C sources):
Deallocating an instance of a *Python subclass* of the C ``Semaphore`` type
used to corrupt the heap: the C deallocs freed with a bare
``PyObject_Del(self)`` instead of ``Py_TYPE(self)->tp_free(self)``, which is
wrong for GC-tracked heap-subclass instances allocated via
``PyType_GenericAlloc``.  ``filament.threading.BoundedSemaphore`` is exactly
such a subclass and triggered nondeterministic hangs/segfaults in later,
unrelated allocations.  All the C deallocs now respect ``tp_free``.  The
BoundedSemaphore tests below still run in a fresh subprocess via ``run_py``:
that keeps them valid as a regression canary independent of in-process heap
state.
"""

from __future__ import absolute_import

import pytest

import filament
from filament import threading as fil_threading

from tests._helpers import run_py, PREAMBLE


def run(fn):
    return filament.spawn(fn).wait()


# --------------------------------------------------------------------------- #
# get_ident
# --------------------------------------------------------------------------- #

def test_get_ident_unique_per_greenthread():
    main_ident = fil_threading.get_ident()
    assert isinstance(main_ident, int)
    other = run(fil_threading.get_ident)
    assert isinstance(other, int)
    assert other != main_ident


# --------------------------------------------------------------------------- #
# BoundedSemaphore
# --------------------------------------------------------------------------- #

# Run in a fresh subprocess: see the dealloc-corruption NOTE in the module
# docstring.  References are kept alive and the child leaves via os._exit so
# the broken subclass dealloc never runs.
BOUNDED_SEM_SCRIPT = PREAMBLE + """
import filament
from filament import threading as fil_threading

_keep = []  # never dealloc a C-Semaphore subclass instance (heap corruption)


def body():
    sem = fil_threading.BoundedSemaphore(2)
    _keep.append(sem)
    assert sem._initial_value == 2
    assert sem.counter == 2
    sem.acquire()
    sem.acquire()
    sem.release()
    sem.release()
    try:
        sem.release()          # released above the ceiling
    except ValueError:
        pass
    else:
        raise AssertionError('expected ValueError (value=2)')

    sem2 = fil_threading.BoundedSemaphore()   # default value=1
    _keep.append(sem2)
    try:
        sem2.release()
    except ValueError:
        pass
    else:
        raise AssertionError('expected ValueError (default value)')
    return 'ok'


assert filament.spawn(body).wait() == 'ok'
_done('BOUNDED-OK')
"""


def test_bounded_semaphore_semantics_in_subprocess():
    res = run_py(BOUNDED_SEM_SCRIPT, timeout=30)
    assert res.ok(), repr(res)
    assert 'BOUNDED-OK' in res.stdout


# --------------------------------------------------------------------------- #
# _CondEvent (Condition-based Event fallback, tested directly)
# --------------------------------------------------------------------------- #

def test_condevent_set_clear_is_set():
    def body():
        e = fil_threading._CondEvent()
        assert not e.is_set()
        assert not e.isSet()  # deprecated alias
        e.set()
        assert e.is_set()
        assert e.isSet()
        # wait() on an already-set event returns True immediately.
        assert e.wait() is True
        e.clear()
        assert not e.is_set()
        return 'ok'

    assert run(body) == 'ok'


def test_condevent_wait_woken_by_other_greenthread():
    def body():
        e = fil_threading._CondEvent()

        def setter():
            filament.sleep(0.02)
            e.set()

        g = filament.spawn(setter)
        result = e.wait()
        g.wait()
        return result

    assert run(body) is True


def _classify(ex):
    return (type(ex).__module__, type(ex).__name__)


def test_condevent_wait_timeout_returns_false():
    # Stdlib Event.wait(timeout) contract: expiry returns the flag state
    # (False), it does not raise.
    def body():
        e = fil_threading._CondEvent()
        return e.wait(timeout=0.05)

    assert run(body) is False


def test_thread_join_timeout_returns_quietly():
    # Stdlib join(timeout) contract: expiry returns quietly (None) with the
    # thread still alive.
    def body():
        gate = fil_threading._CondEvent()
        t = fil_threading.Thread(target=gate.wait)
        t.start()
        result = t.join(timeout=0.05)
        alive = t.is_alive()
        gate.set()
        t.join()
        return (result, alive)

    assert run(body) == (None, True)


# --------------------------------------------------------------------------- #
# Thread: constructor validation and lifecycle errors
# --------------------------------------------------------------------------- #

def test_thread_rejects_group():
    with pytest.raises(ValueError):
        fil_threading.Thread(group=object())


def test_thread_double_start_raises():
    def body():
        t = fil_threading.Thread(target=lambda: None)
        t.start()
        with pytest.raises(RuntimeError):
            t.start()
        t.join()
        return 'ok'

    assert run(body) == 'ok'


def test_thread_join_before_start_raises():
    t = fil_threading.Thread(target=lambda: None)
    with pytest.raises(RuntimeError):
        t.join()


def test_thread_run_without_target_is_noop():
    # run() with no target does nothing (and does not blow up).
    t = fil_threading.Thread()
    assert t.run() is None


# --------------------------------------------------------------------------- #
# Thread: lifecycle / is_alive
# --------------------------------------------------------------------------- #

def test_thread_is_alive_lifecycle():
    def body():
        gate = fil_threading._CondEvent()
        t = fil_threading.Thread(target=gate.wait)
        assert not t.is_alive()          # not started yet
        t.start()
        filament.sleep(0)                # let it get into the target
        assert t.is_alive()
        assert t.isAlive()               # deprecated alias
        gate.set()
        t.join()
        assert not t.is_alive()
        return 'ok'

    assert run(body) == 'ok'


# --------------------------------------------------------------------------- #
# Thread: name / ident / daemon accessors
# --------------------------------------------------------------------------- #

def test_thread_name_accessors():
    t = fil_threading.Thread(name='worker')
    assert t.name == 'worker'
    assert t.getName() == 'worker'
    t.name = 'renamed'
    assert t.name == 'renamed'
    t.setName('renamed-again')
    assert t.getName() == 'renamed-again'
    # Default names are generated from a counter.
    t2 = fil_threading.Thread()
    assert t2.name.startswith('Thread-')


def test_thread_ident_set_after_start():
    def body():
        t = fil_threading.Thread(target=lambda: None)
        assert t.ident is None
        t.start()
        t.join()
        return t.ident

    assert isinstance(run(body), int)


def test_thread_daemon_accessors():
    t = fil_threading.Thread()
    assert t.daemon is False
    assert t.isDaemon() is False
    t.daemon = True
    assert t.daemon is True
    t.setDaemon(0)
    assert t.isDaemon() is False
    t2 = fil_threading.Thread(daemon=True)
    assert t2.daemon is True


# --------------------------------------------------------------------------- #
# current_thread / active_count / enumerate
# --------------------------------------------------------------------------- #

def test_current_thread_inside_and_outside():
    def body():
        seen = []

        def work():
            seen.append(fil_threading.current_thread())

        t = fil_threading.Thread(target=work, name='inner')
        t.start()
        t.join()
        # Inside the spawned Thread, current_thread() is that Thread.
        assert seen == [t]
        # A raw greenthread (this body) gets the MainThread stand-in.
        cur = fil_threading.current_thread()
        assert cur.name == 'MainThread'
        assert fil_threading.currentThread() is cur  # deprecated alias
        return 'ok'

    assert run(body) == 'ok'


def test_active_count_and_enumerate():
    def body():
        gate = fil_threading._CondEvent()
        base_count = fil_threading.active_count()
        assert base_count >= 1  # the main thread stand-in
        t = fil_threading.Thread(target=gate.wait)
        t.start()
        filament.sleep(0)  # let it register itself
        assert fil_threading.active_count() == base_count + 1
        assert fil_threading.activeCount() == base_count + 1  # alias
        threads = fil_threading.enumerate()
        assert t in threads
        assert threads[0].name == 'MainThread'
        gate.set()
        t.join()
        assert fil_threading.active_count() == base_count
        assert t not in fil_threading.enumerate()
        return 'ok'

    assert run(body) == 'ok'


# --------------------------------------------------------------------------- #
# local
# --------------------------------------------------------------------------- #

def test_local_is_greenthread_local():
    def body():
        loc = fil_threading.local()
        loc.value = 'main'

        def other():
            loc.value = 'other'
            return loc.value

        assert filament.spawn(other).wait() == 'other'
        return loc.value

    assert run(body) == 'main'


# --------------------------------------------------------------------------- #
# Timer
# --------------------------------------------------------------------------- #

def test_timer_cancel_prevents_call():
    def body():
        out = []
        t = fil_threading.Timer(0.05, out.append, args=('fired',))
        assert t.interval == 0.05
        t.start()
        t.cancel()
        t.join()
        filament.sleep(0.1)  # past the interval; must still not fire
        return (out, t.finished.is_set())

    out, finished = run(body)
    assert out == []
    assert finished is True


def test_timer_fires_after_interval():
    def body():
        out = []
        t = fil_threading.Timer(0.05, out.append, args=('fired',))
        t.start()
        t.join()
        filament.sleep(0.1)
        return out

    assert run(body) == ['fired']


def test_timer_set_then_clear_fires_early():
    # set() wakes the waiting timer greenthread, and an immediate clear()
    # makes it observe a False flag when it resumes -- so the callback runs
    # right away instead of after the (long) interval.
    def body():
        out = []
        t = fil_threading.Timer(30.0, lambda a, b: out.append(a + b),
                                args=(1,), kwargs={'b': 2})
        t.start()
        filament.sleep(0.02)  # let the timer block in finished.wait()
        t.finished.set()
        t.finished.clear()
        t.join()
        return out

    assert run(body) == [3]


# --------------------------------------------------------------------------- #
# copied-through stdlib surface
# --------------------------------------------------------------------------- #

def test_stdlib_bits_copied_across():
    # copy_globals brings across the stdlib pieces we did not override.
    assert hasattr(fil_threading, 'ThreadError')
    assert fil_threading.Event is not None
