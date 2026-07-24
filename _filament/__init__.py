"""
``_filament`` -- namespace package for filament's compiled C extensions.

Each compiled extension is a submodule imported explicitly by name
(``_filament.core``, ``_filament.io``, ``_filament.socket``, ``_filament.queue``,
``_filament.locking``, ``_filament.timer``, ``_filament.thrpool``).  The
high-level, user-facing API lives in the pure-Python ``filament`` package.

This __init__ is intentionally passive.  It previously did
``from _filament.core import *``, but on Python 2 that turned importing any
C submodule (whose init calls ``PyImport_ImportModule("_filament.core")`` to
grab the shared C-API capsule) into a re-entrant import of a half-initialized
``_filament`` package, raising ``AttributeError: 'module' object has no
attribute 'core'``.  Keeping this file free of imports avoids that cycle and is
harmless on Python 3 (nothing imports bare ``_filament.<name>``; every consumer
uses ``from _filament.core import ...`` and friends).
"""
