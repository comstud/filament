# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.eventlet_compat
========================

A drop-in compatibility shim that lets code written against **eventlet** run on
filament unchanged.

Design (important): we do NOT ship a top-level ``eventlet/`` package on disk --
that would shadow the *real* eventlet for anything importing it from the repo
root.  Instead :func:`install` registers filament-backed modules into
``sys.modules`` under the ``eventlet`` names, but only when explicitly called.
Until you call ``install()``, ``import eventlet`` still finds the genuine
library.

Usage::

    import filament.eventlet_compat as ec
    ec.install()
    import eventlet          # now resolves to the filament-backed shim
"""

from __future__ import absolute_import

import sys

# Green stdlib modules live in filament.* and are simply re-registered under the
# eventlet.green.* names -- no per-module wrapper needed.
import filament.socket as _green_socket
import filament.ssl as _green_ssl
import filament.select as _green_select
import filament.os as _green_os
import filament.time as _green_time
import filament.threading as _green_threading
import filament.thread as _green_thread
import filament.subprocess as _green_subprocess
import filament.queue as _green_queue

from filament.eventlet_compat import greenthread
from filament.eventlet_compat import main
from filament.eventlet_compat import event
from filament.eventlet_compat import queue
from filament.eventlet_compat import semaphore
from filament.eventlet_compat import timeout
from filament.eventlet_compat import hubs


def _make_green_package():
    """
    Build the ``eventlet.green`` package module.

    eventlet.green is a package whose submodules are cooperative stand-ins for
    the stdlib.  We create a bare module object, hang the filament green modules
    off it as attributes, and (in install()) also register each under its dotted
    ``eventlet.green.<name>`` sys.modules key so ``import eventlet.green.socket``
    works.
    """
    import types
    pkg = types.ModuleType("eventlet.green")
    pkg.__path__ = []  # marks it as a package for importlib
    pkg.socket = _green_socket
    pkg.ssl = _green_ssl
    pkg.select = _green_select
    pkg.os = _green_os
    pkg.time = _green_time
    pkg.threading = _green_threading
    pkg.thread = _green_thread
    pkg.subprocess = _green_subprocess
    # Both spellings so patched code works on py2 (Queue) and py3 (queue).
    pkg.Queue = _green_queue
    pkg.queue = _green_queue
    return pkg


def _make_greenpool_module():
    """Build ``eventlet.greenpool`` exposing GreenPool / GreenPile."""
    import types
    import filament
    mod = types.ModuleType("eventlet.greenpool")
    mod.GreenPool = filament.GreenPool
    mod.GreenPile = filament.GreenPile
    return mod


# The green package object is shared between the attribute on ``eventlet`` and
# the sys.modules registration.
_green_pkg = _make_green_package()
_greenpool_mod = _make_greenpool_module()

# Expose ``eventlet.green`` and ``eventlet.hubs`` as attributes of the top-level
# module too (so ``eventlet.green.socket`` attribute access resolves).
main.green = _green_pkg
main.hubs = hubs
main.greenpool = _greenpool_mod

# The full name -> module registration table applied by install().
_MODULE_MAP = {
    "eventlet": main,
    "eventlet.greenthread": greenthread,
    "eventlet.event": event,
    "eventlet.queue": queue,
    "eventlet.semaphore": semaphore,
    "eventlet.timeout": timeout,
    "eventlet.hubs": hubs,
    "eventlet.greenpool": _greenpool_mod,
    # The green package + its submodules.
    "eventlet.green": _green_pkg,
    "eventlet.green.socket": _green_socket,
    "eventlet.green.ssl": _green_ssl,
    "eventlet.green.select": _green_select,
    "eventlet.green.os": _green_os,
    "eventlet.green.time": _green_time,
    "eventlet.green.threading": _green_threading,
    "eventlet.green.thread": _green_thread,
    "eventlet.green.subprocess": _green_subprocess,
    "eventlet.green.Queue": _green_queue,
    "eventlet.green.queue": _green_queue,
}


def install():
    """
    Register the filament-backed eventlet shim into ``sys.modules``.

    Idempotent: safe to call more than once (it simply re-points the same
    entries).  After this returns, ``import eventlet`` (and the documented
    submodules) resolve to filament-backed implementations.
    """
    for name, mod in _MODULE_MAP.items():
        sys.modules[name] = mod


def uninstall():
    """
    Remove the shim entries from ``sys.modules`` (best-effort).

    Only removes entries that are still *our* shim objects, so we never clobber
    a real eventlet a caller may have imported afterwards.
    """
    for name, mod in _MODULE_MAP.items():
        if sys.modules.get(name) is mod:
            del sys.modules[name]
