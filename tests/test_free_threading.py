# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Free-threaded (PEP 703) builds keep the GIL disabled.

A free-threaded interpreter switches the GIL back ON, for the whole process
and for good, the moment it imports an extension module that has not declared
``Py_mod_gil = Py_MOD_GIL_NOT_USED``.  Nothing raises, nothing is logged at
default verbosity, and every other test in this suite still passes -- against
the stock runtime, having measured nothing about free-threading.  That is the
failure this file exists to catch.

Every filament extension module is imported first, not just the top-level
package, because one undeclared module anywhere is enough to flip the switch.

Inert on ordinary builds, where there is no such property to check.
"""
import sys
import sysconfig

import pytest

import filament


FREE_THREADED = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))

needs_free_threading = pytest.mark.skipif(
    not FREE_THREADED, reason="not a free-threaded build")


@needs_free_threading
def test_gil_stays_disabled_after_importing_filament():
    assert not sys._is_gil_enabled(), (
        "importing filament re-enabled the GIL: some extension module is "
        "missing Py_mod_gil = Py_MOD_GIL_NOT_USED")


@needs_free_threading
def test_gil_stays_disabled_after_importing_every_extension():
    # Importing the package does not necessarily pull in every extension.
    import filament.gevent_compat  # noqa: F401
    import filament.socket  # noqa: F401
    import filament.thread  # noqa: F401
    import filament.time  # noqa: F401
    import filament.tpool  # noqa: F401

    assert not sys._is_gil_enabled(), (
        "the GIL came back after importing filament's submodules")


def test_filament_imports():
    # Guards the two above: if the import at module scope ever fails, the
    # skipif would hide it and this file would silently test nothing.
    assert filament is not None
