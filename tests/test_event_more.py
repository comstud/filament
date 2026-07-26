# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Additional filament.event coverage (additive to test_event.py).

Covers the _reraise edge paths (None exc_value, mismatched traceback), the
outer with-Timeout propagation branches in Event.wait / AsyncResult.get /
AsyncResult.wait, the exc_info property, the link-target ``__call__``
failure branch, and the eventlet send/send_exception argument forms.
"""

from __future__ import absolute_import

import sys

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


# --------------------------------------------------------------------------- #
# _reraise edge paths (exercised through AsyncResult.get on a ready result)
# --------------------------------------------------------------------------- #

def test_get_reraise_with_none_value_instantiates_type():
    ar = filament.AsyncResult()
    # Make it ready first so set_exception() skips the C Message and get()
    # takes the pure-python _reraise path with our crafted exc_info.
    ar.set(1)
    ar.set_exception(ValueError("unused"), (ValueError, None, None))
    with pytest.raises(ValueError):
        ar.get()


@pytest.mark.skipif(sys.version_info[0] < 3,
                    reason='__traceback__ surgery is py3-only')
def test_get_reraise_attaches_foreign_traceback():
    # exc_value whose __traceback__ differs from the stored tb: _reraise must
    # re-attach the stored traceback via with_traceback().
    try:
        raise KeyError("origin-frame")
    except KeyError:
        tb = sys.exc_info()[2]
    fresh = RuntimeError("fresh-instance")
    assert fresh.__traceback__ is not tb

    ar = filament.AsyncResult()
    ar.set(1)                      # ready: get() uses _reraise directly
    ar.set_exception(fresh, (RuntimeError, fresh, tb))
    try:
        ar.get()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        names = []
        t = e.__traceback__
        while t is not None:
            names.append(t.tb_frame.f_code.co_name)
            t = t.tb_next
        # The foreign traceback (from the KeyError raise site) was attached.
        assert "test_get_reraise_attaches_foreign_traceback" in names


# --------------------------------------------------------------------------- #
# Outer with-Timeout propagation (vs. the primitive's own wait timeout)
# --------------------------------------------------------------------------- #

def test_event_wait_outer_timeout_propagates():
    ev = filament.Event()

    def run():
        with filament.Timeout(0.05):
            ev.wait(0.5)

    with pytest.raises(filament.Timeout):
        filament.spawn(run).wait()


def test_asyncresult_get_outer_timeout_propagates():
    ar = filament.AsyncResult()

    def run():
        with filament.Timeout(0.05):
            ar.get(timeout=0.5)

    with pytest.raises(filament.Timeout):
        filament.spawn(run).wait()


def test_asyncresult_wait_outer_timeout_propagates():
    ar = filament.AsyncResult()

    def run():
        with filament.Timeout(0.05):
            ar.wait(0.5)

    with pytest.raises(filament.Timeout):
        filament.spawn(run).wait()


def test_asyncresult_wait_own_timeout_returns_none():
    ar = filament.AsyncResult()
    # wait()'s own timeout: returns None, never raises.
    assert filament.spawn(lambda: ar.wait(0.05)).wait() is None
    assert ar.ready() is False


# --------------------------------------------------------------------------- #
# exc_info property (gevent parity)
# --------------------------------------------------------------------------- #

def test_exc_info_property_when_failed():
    ar = filament.AsyncResult()
    err = ValueError("boom")
    ar.set_exception(err)
    info = ar.exc_info
    assert info is not None
    assert info[0] is ValueError
    assert info[1] is err


def test_exc_info_property_when_not_failed():
    ar = filament.AsyncResult()
    assert ar.exc_info is None
    ar.set("v")
    assert ar.exc_info is None


# --------------------------------------------------------------------------- #
# __call__ -- the gevent link-target protocol
# --------------------------------------------------------------------------- #

def test_call_adopts_successful_source():
    src = filament.AsyncResult()
    src.set(42)
    tgt = filament.AsyncResult()
    assert tgt(src) is tgt
    assert tgt.successful() is True
    assert tgt.value == 42


def test_call_adopts_failed_source():
    src = filament.AsyncResult()
    src.set_exception(ValueError("linked-boom"))
    tgt = filament.AsyncResult()
    assert tgt(src) is tgt
    assert tgt.successful() is False
    assert isinstance(tgt.exception, ValueError)
    with pytest.raises(ValueError):
        tgt.get()


# --------------------------------------------------------------------------- #
# eventlet send / send_exception argument forms
# --------------------------------------------------------------------------- #

def test_send_with_exc_obj_stores_exception():
    ar = filament.AsyncResult()
    ar.send(exc_obj=ValueError("via-exc-obj"))
    assert ar.successful() is False
    with pytest.raises(ValueError):
        ar.get()


def test_send_exception_with_full_triple():
    ar = filament.AsyncResult()
    try:
        raise RuntimeError("triple-boom")
    except RuntimeError:
        ar.send_exception(*sys.exc_info())
    try:
        filament.spawn(ar.get).wait()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "triple-boom" in str(e)
    assert isinstance(ar.exception, RuntimeError)


def test_send_exception_type_only():
    ar = filament.AsyncResult()
    # (type, None): the value is synthesized by instantiating the type.
    ar.send_exception(ValueError, None)
    assert isinstance(ar.exception, ValueError)
    with pytest.raises(ValueError):
        ar.get()
