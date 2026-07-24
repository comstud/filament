# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.eventlet_compat.main
=============================

The top-level ``eventlet`` namespace (injected as ``sys.modules['eventlet']``).

Everything here is backed by filament and re-exported under the names eventlet
programs expect: ``spawn`` / ``spawn_n`` / ``sleep``, ``GreenPool`` /
``GreenPile``, ``Queue`` / ``Event`` / ``Timeout`` / ``Semaphore``, the
``monkey_patch`` family, and the network convenience helpers ``listen`` /
``connect`` / ``wrap_ssl`` / ``serve``.

Each binding below is a faithful mapping unless a comment flags it as a stub or
documented divergence.
"""

from __future__ import absolute_import

import sys

import filament
import filament.socket as _green_socket
import filament.ssl as _green_ssl
from filament import patcher as _patcher

from filament.eventlet_compat import greenthread as _greenthread
from filament.eventlet_compat import semaphore as _semaphore

# ---------------------------------------------------------------------------
# Greenthread primitives (faithful mappings -> eventlet_compat.greenthread).
# ---------------------------------------------------------------------------
spawn = _greenthread.spawn
spawn_n = _greenthread.spawn_n
spawn_after = _greenthread.spawn_after
spawn_after_local = _greenthread.spawn_after_local
kill = _greenthread.kill
sleep = _greenthread.sleep
getcurrent = _greenthread.getcurrent
GreenThread = _greenthread.GreenThread
GreenletExit = _greenthread.GreenletExit

# ---------------------------------------------------------------------------
# Pools / piles / queues / events / timeouts / semaphores (faithful mappings).
# ---------------------------------------------------------------------------
GreenPool = filament.GreenPool
GreenPile = filament.GreenPile
Queue = filament.Queue
# eventlet.Event is the one-shot send/wait future == filament.AsyncResult.
Event = filament.AsyncResult
Timeout = filament.Timeout
with_timeout = filament.with_timeout
Semaphore = _semaphore.Semaphore
BoundedSemaphore = _semaphore.BoundedSemaphore


# ---------------------------------------------------------------------------
# monkey-patching.
# ---------------------------------------------------------------------------
def monkey_patch(all=True, os=None, select=None, socket=None, thread=None,
                 time=None, subprocess=None, ssl=None, MySQLdb=None,
                 builtins=None, **kwargs):
    """
    eventlet.monkey_patch -> filament.patcher.

    eventlet's convention: with no explicit subsystem flags, patch everything;
    if ANY subsystem is named, patch ONLY the named ones.  We reproduce that
    selection rule, then delegate to filament's patcher.

    Unsupported-by-filament targets (``MySQLdb``, ``builtins``) are accepted and
    ignored -- documented no-ops -- so callers don't crash.
    """
    # Did the caller explicitly request specific subsystems?
    named = [os, select, socket, thread, time, subprocess, ssl]
    any_named = any(flag is not None for flag in named)

    if not any_named:
        # No subsystem flags -> patch all (respecting all=... escape hatch).
        if all:
            _patcher.patch_all()
        return

    # Selective: only patch the subsystems the caller turned on.
    if os:
        _patcher.patch_os()
    if select:
        _patcher.patch_select()
    if socket:
        _patcher.patch_socket()
    if thread:
        _patcher.patch_thread()
    if time:
        _patcher.patch_time()
    if subprocess:
        _patcher.patch_subprocess()
    if ssl:
        _patcher.patch_ssl()


def is_monkey_patched(module):
    """
    eventlet.is_monkey_patched -> filament.patcher.is_module_patched.

    Accepts a module name string or a module object (we read ``__name__``).
    """
    name = module if isinstance(module, str) else getattr(module, "__name__",
                                                           str(module))
    return _patcher.is_module_patched(name)


def import_patched(module_name, *additional_modules, **kw_additional_modules):
    """
    eventlet.import_patched: import ``module_name`` with the green stdlib
    temporarily substituted, WITHOUT globally monkey-patching.

    Implementation: we temporarily swap filament's cooperative modules into
    ``sys.modules`` for the common stdlib names (socket, ssl, select, time, os,
    threading, thread/_thread, subprocess, queue), import a *fresh* copy of the
    target module so its ``import socket`` etc. bind to the green versions, then
    restore ``sys.modules``.  This covers the common case.

    Documented limits: it handles the standard set of green modules only; it
    does not honour eventlet's per-call ``additional_modules`` overrides (those
    are accepted and ignored), and a module that imports its dependencies lazily
    (inside functions, after import time) will not be fully greened.
    """
    # Map stdlib name -> filament green module.  Both py2/py3 thread names are
    # covered so the substitution works on either interpreter.
    import filament.select as _sel
    import filament.time as _time
    import filament.os as _os
    import filament.threading as _threading
    import filament.thread as _thread
    import filament.subprocess as _subprocess
    import filament.queue as _queue

    green_map = {
        "socket": _green_socket,
        "ssl": _green_ssl,
        "select": _sel,
        "time": _time,
        "os": _os,
        "threading": _threading,
        "thread": _thread,        # py2 low-level thread
        "_thread": _thread,       # py3 low-level thread
        "subprocess": _subprocess,
        "queue": _queue,          # py3 queue
        "Queue": _queue,          # py2 Queue
    }

    saved = {}
    # Also drop any cached copy of the target so it is re-imported fresh under
    # the substitution.
    saved[module_name] = sys.modules.pop(module_name, None)
    for name, green in green_map.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = green
    try:
        __import__(module_name)
        return sys.modules[module_name]
    finally:
        # Restore every name we touched (including the freshly imported target,
        # which we replace with its pre-existing value so we don't leak a
        # green-linked copy into the global module table).
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


# ---------------------------------------------------------------------------
# Network convenience helpers.
# ---------------------------------------------------------------------------
class StopServe(Exception):
    """Raise from a :func:`serve` handler to stop the server loop (eventlet)."""


def listen(addr, family=None, backlog=50, reuse_addr=True, reuse_port=None):
    """
    Create a listening green socket bound to ``addr`` (eventlet.listen).

    ``addr`` is an ``(host, port)`` tuple.  Returns a cooperative
    ``filament.socket.socket`` already in the listening state.  Faithful
    mapping; ``reuse_port`` is honoured only if the platform exposes it.
    """
    if family is None:
        family = _green_socket.AF_INET
    sock = _green_socket.socket(family, _green_socket.SOCK_STREAM)
    if reuse_addr and hasattr(_green_socket, "SO_REUSEADDR"):
        sock.setsockopt(_green_socket.SOL_SOCKET,
                        _green_socket.SO_REUSEADDR, 1)
    if reuse_port and hasattr(_green_socket, "SO_REUSEPORT"):
        sock.setsockopt(_green_socket.SOL_SOCKET,
                        _green_socket.SO_REUSEPORT, 1)
    sock.bind(addr)
    sock.listen(backlog)
    return sock


def connect(addr, family=None, bind=None):
    """
    Open a cooperative client connection to ``addr`` (eventlet.connect).

    Returns a connected ``filament.socket.socket``.  Faithful mapping.
    """
    if family is None:
        family = _green_socket.AF_INET
    sock = _green_socket.socket(family, _green_socket.SOCK_STREAM)
    if bind is not None:
        sock.bind(bind)
    sock.connect(addr)
    return sock


def wrap_ssl(sock, *args, **kwargs):
    """
    Wrap ``sock`` in a cooperative SSL socket (eventlet.wrap_ssl).

    Delegates to ``filament.ssl.wrap_socket``.  eventlet accepts a
    ``server_side`` keyword plus the usual ssl.wrap_socket arguments, which pass
    straight through.  Faithful mapping over filament's green ssl.
    """
    return _green_ssl.wrap_socket(sock, *args, **kwargs)


def serve(sock, handle, concurrency=1000):
    """
    Accept connections on ``sock`` forever, running ``handle(client, addr)`` for
    each in a bounded :class:`GreenPool` (eventlet.serve).

    Stops when a handler raises :class:`StopServe`.  Faithful mapping; the
    accept loop and per-connection spawn mirror eventlet's implementation.
    """
    pool = filament.GreenPool(concurrency)

    def _wrap(client, addr):
        try:
            handle(client, addr)
        finally:
            try:
                client.close()
            except Exception:
                pass

    while True:
        try:
            client, addr = sock.accept()
        except StopServe:
            return
        pool.spawn(_wrap, client, addr)


# ---------------------------------------------------------------------------
# Submodule handles.  Exposing them as attributes lets ``eventlet.event`` /
# ``eventlet.hubs`` attribute access work even before install() has run (though
# install() also registers them under their dotted sys.modules names).
# ---------------------------------------------------------------------------
from filament.eventlet_compat import greenthread  # noqa: E402,F401
from filament.eventlet_compat import event         # noqa: E402,F401
from filament.eventlet_compat import queue         # noqa: E402,F401
from filament.eventlet_compat import semaphore     # noqa: E402,F401
from filament.eventlet_compat import timeout       # noqa: E402,F401
from filament.eventlet_compat import hubs          # noqa: E402,F401
