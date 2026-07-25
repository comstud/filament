# Third-party notices

Filament is MIT-licensed (see `LICENSE`). It incorporates or derives from the
following third-party code:

## Vendored greenlet

`vendor/greenlet/` is a vendored copy of
[greenlet](https://github.com/python-greenlet/greenlet) 3.5.4 with
filament-specific modifications, remaining under greenlet's own licenses:
MIT-style (`vendor/greenlet/LICENSE`) plus the Python Software Foundation
License for its Stackless-derived platform switch files
(`vendor/greenlet/LICENSE.PSF`). Provenance and the list of local changes are
documented in `vendor/greenlet/VENDORED.md`.

## Portions derived from CPython

The following portions are derived from CPython's standard library and are
used under the Python Software Foundation License Version 2
(see `LICENSE.PSF`; Copyright © Python Software Foundation):

- `filament/pyqueue.py` — the overridable-hook structure and the
  `task_done()`/`join()` logic follow CPython's `Lib/queue.py`.
- `filament/threading.py` — the `Timer` class closely follows CPython's
  `threading.Timer` (and adopts its docstrings at runtime where noted).
- `filament/_threading_local.py` — implements the attribute-swapping design of
  CPython's `Lib/_threading_local.py`, adapted to per-greenthread storage.
- `filament/subprocess.py` — the `call()`/`check_output()` wrappers follow the
  canonical CPython `subprocess` idiom.
- `src/socket/fil_socket.c` — the `SOCKET_T`/`INVALID_SOCKET`/
  `PyLong_{From,As}Socket_t` portability block is taken from CPython's
  `Modules/socketmodule.h`.

No code from gevent or eventlet is included; filament's `gevent_compat` and
`eventlet_compat` packages are original implementations of those libraries'
public APIs.
