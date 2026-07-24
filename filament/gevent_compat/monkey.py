# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.monkey
=============================

Drop-in replacement for ``gevent.monkey`` (injected as
``sys.modules['gevent.monkey']``).

Every function delegates to :mod:`filament.patcher`, which does the real
cooperative monkey-patching.  ``patch_all`` and the granular ``patch_*``
helpers are faithful mappings; the introspection helpers (``is_module_patched``,
``is_object_patched``, ``get_original``, ``saved``) are re-exported straight
from the patcher.
"""

from __future__ import absolute_import

from filament import patcher as _patcher

# Faithful re-exports of the patcher's introspection surface.
is_module_patched = _patcher.is_module_patched
is_object_patched = _patcher.is_object_patched
get_original = _patcher.get_original
# gevent exposes the saved-originals table as ``monkey.saved``.
saved = _patcher.saved


def patch_all(socket=True, dns=True, time=True, select=True, thread=True,
              os=True, ssl=True, subprocess=True, sys=False, aggressive=True,
              Event=False, builtins=True, signal=True, queue=True, **kwargs):
    """
    gevent.monkey.patch_all -> filament.patcher.patch_all.

    filament supports the socket/dns/time/select/thread/os/ssl/subprocess/queue
    subsystems; gevent-only toggles (``sys``, ``builtins``, ``signal``,
    ``Event``) are accepted and ignored (documented no-ops) so gevent-shaped
    calls don't crash.
    """
    _patcher.patch_all(socket=socket, dns=dns, time=time, select=select,
                       thread=thread, os=os, ssl=ssl, subprocess=subprocess,
                       queue=queue, aggressive=aggressive)


def patch_socket(dns=True, aggressive=True):
    """Faithful mapping onto filament.patcher.patch_socket."""
    return _patcher.patch_socket(dns=dns, aggressive=aggressive)


def patch_ssl(*args, **kwargs):
    """Faithful mapping onto filament.patcher.patch_ssl."""
    return _patcher.patch_ssl()


def patch_select(aggressive=True):
    """Faithful mapping onto filament.patcher.patch_select."""
    return _patcher.patch_select()


def patch_os():
    """Faithful mapping onto filament.patcher.patch_os."""
    return _patcher.patch_os()


def patch_time():
    """Faithful mapping onto filament.patcher.patch_time."""
    return _patcher.patch_time()


def patch_thread(threading=True, _threading_local=True, Event=True,
                 logging=True, existing_locks=True, **kwargs):
    """Faithful mapping onto filament.patcher.patch_thread."""
    return _patcher.patch_thread(threading=threading,
                                 _threading_local=_threading_local,
                                 Event=Event, logging=logging,
                                 existing_locks=existing_locks)


def patch_subprocess():
    """Faithful mapping onto filament.patcher.patch_subprocess."""
    return _patcher.patch_subprocess()


def patch_queue():
    """Faithful mapping onto filament.patcher.patch_queue."""
    return _patcher.patch_queue()


def patch_dns():
    """Faithful mapping onto filament.patcher.patch_dns."""
    return _patcher.patch_dns()


__all__ = ["patch_all", "patch_socket", "patch_ssl", "patch_select",
           "patch_os", "patch_time", "patch_thread", "patch_subprocess",
           "patch_queue", "patch_dns", "is_module_patched",
           "is_object_patched", "get_original", "saved"]
