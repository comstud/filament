# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
pytest configuration + safety nets for the filament test suite.

Two things happen here:

1. **Per-test watchdog.**  filament is a cooperative runtime; a genuine deadlock
   would otherwise hang the whole run forever (there is no ``pytest-timeout`` in
   the interpreter farm).  We arm ``faulthandler.dump_traceback_later`` around
   every test so a stuck test dumps all thread tracebacks and hard-exits instead
   of hanging.  In normal operation the watchdog is cancelled at teardown and
   has zero effect.

2. **Thread-pool teardown.**  ``filament.tpool`` keeps a pool of real OS worker
   threads alive.  We shut it down at the end of the session so the interpreter
   exits cleanly.

Everything is written to import cleanly on Python 2.7 as well (pytest may be old
there; we avoid any modern-only fixtures/APIs).
"""

from __future__ import absolute_import

import sys

try:
    import faulthandler
    _HAVE_FAULTHANDLER = True
except ImportError:  # pragma: no cover - py2.7 has no faulthandler
    _HAVE_FAULTHANDLER = False

import pytest


# Generous per-test wall-clock budget.  Real tests finish in well under a
# second; anything approaching this is a hang.
_PER_TEST_TIMEOUT = 45


@pytest.fixture(autouse=True)
def _watchdog():
    """Abort (with tracebacks) any single test that runs longer than the budget."""
    if _HAVE_FAULTHANDLER:
        faulthandler.dump_traceback_later(_PER_TEST_TIMEOUT, exit=True)
    try:
        yield
    finally:
        if _HAVE_FAULTHANDLER:
            faulthandler.cancel_dump_traceback_later()


def _shutdown_tpool():
    try:
        import filament.tpool as tpool
        tpool.shutdown()
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    _shutdown_tpool()
