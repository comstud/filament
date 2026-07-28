# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat
======================

A drop-in compatibility shim that lets code written against **gevent** run on
filament unchanged.

Design (important, mirrors :mod:`filament.eventlet_compat`): we do NOT ship a
top-level ``gevent/`` package on disk -- that would shadow the *real* gevent for
anything importing it from the repo root.  Instead :func:`install` registers
filament-backed modules into ``sys.modules`` under the ``gevent`` names, but
only when explicitly called.  Until then, ``import gevent`` still finds the
genuine library.

Usage::

    import filament.gevent_compat as gc
    gc.install()
    import gevent                 # now resolves to the filament-backed shim
    from gevent import monkey
    monkey.patch_all()
"""

from __future__ import absolute_import

import sys

# Green stdlib modules re-registered under gevent.* names (no wrapper needed --
# filament's cooperative modules already carry the right API surface).
import filament.socket as _green_socket
import filament.ssl as _green_ssl
import filament.select as _green_select
import filament.subprocess as _green_subprocess
import filament.os as _green_os
import filament.time as _green_time
import filament.threading as _green_threading
import filament.timeout as _timeout

from filament.gevent_compat import main
from filament.gevent_compat import greenlet as _greenlet
from filament.gevent_compat import rawgreenlet as _rawgreenlet
from filament.gevent_compat import event
from filament.gevent_compat import lock
from filament.gevent_compat import pool
from filament.gevent_compat import queue
from filament.gevent_compat import hub
from filament.gevent_compat import threadpool
from filament.gevent_compat import monkey
from filament.gevent_compat import server
from filament.gevent_compat import pywsgi


# Expose the commonly attribute-accessed submodules on the top-level module too
# (so ``gevent.monkey`` / ``gevent.pool`` resolve via attribute access as well
# as via ``import gevent.monkey``).
main.monkey = monkey
main.greenlet = _greenlet
main.timeout = _timeout
main.event = event
main.lock = lock
main.pool = pool
main.queue = queue
main.hub = hub
main.threadpool = threadpool
main.server = server
main.pywsgi = pywsgi
main.socket = _green_socket
main.ssl = _green_ssl
main.select = _green_select
main.subprocess = _green_subprocess
main.os = _green_os
main.time = _green_time
main.threading = _green_threading


# The full name -> module registration table applied by install().
_MODULE_MAP = {
    "gevent": main,
    # The top-level ``greenlet`` package.  Under real gevent
    # ``greenlet.getcurrent()`` IS the running gevent Greenlet, and code in the
    # wild branches on that identity to decide whether it is about to act on
    # itself.  filament switches on its own
    # ``_fil_greenlet`` runtime, so the installed greenlet package can never
    # see our greenthreads; see :mod:`filament.gevent_compat.rawgreenlet`.
    "greenlet": _rawgreenlet,
    # ``from gevent import greenlet`` / ``from gevent.timeout import Timeout``
    # are common in the wild, so these need real entries in
    # sys.modules, not just attributes on the top-level shim.
    "gevent.greenlet": _greenlet,
    "gevent.timeout": _timeout,
    "gevent.event": event,
    "gevent.lock": lock,
    "gevent.pool": pool,
    "gevent.queue": queue,
    "gevent.hub": hub,
    "gevent.threadpool": threadpool,
    "gevent.monkey": monkey,
    "gevent.server": server,
    "gevent.pywsgi": pywsgi,
    # Green stdlib.
    "gevent.socket": _green_socket,
    "gevent.ssl": _green_ssl,
    "gevent.select": _green_select,
    "gevent.subprocess": _green_subprocess,
    "gevent.os": _green_os,
    "gevent.time": _green_time,
    "gevent.threading": _green_threading,
}


def install():
    """
    Register the filament-backed gevent shim into ``sys.modules``.

    Idempotent: safe to call repeatedly.  After this returns, ``import gevent``
    (and the documented submodules) resolve to filament-backed implementations.
    """
    for name, mod in _MODULE_MAP.items():
        sys.modules[name] = mod


def uninstall():
    """
    Remove the shim entries from ``sys.modules`` (best-effort).

    Only removes entries that are still *our* shim objects so we never clobber a
    real gevent a caller may have imported afterwards.
    """
    for name, mod in _MODULE_MAP.items():
        if sys.modules.get(name) is mod:
            del sys.modules[name]
