# Vendored greenlet

This directory contains a vendored copy of **greenlet 3.5.4**
(https://github.com/python-greenlet/greenlet), compiled into filament as the
private extension module `_fil_greenlet` so that filament's performance
modifications cannot conflict with a separately installed greenlet.

Upstream licensing applies and is included here verbatim:

- `LICENSE` — greenlet's MIT-style license. Note its first section: files
  derived from Stackless Python (`slp_platformselect.h` and the `platform/`
  directory) are subject to the Python Software Foundation license instead,
  included as `LICENSE.PSF`.
- `AUTHORS` — greenlet's author list.

## Local modifications (filament)

On top of upstream 3.5.4, this copy carries filament-specific changes, all
clearly marked with `fil_`/`vgl_`/`VGL_`/`FIL_` prefixes or `filament:`
comments:

- A C fast-switch entry (`vgl_fast_switch`, exported via a second capsule
  `_C_FAST_API`) that skips argument marshalling for filament's internal
  no-argument scheduler switches.
- Runtime-selectable debug mode: lazy frame exposure by default on
  CPython 3.12/3.13 with on-access `gr_frame` materialization,
  `set_debug()`/`get_debug()` module functions, and unconditional GC
  traversal of suspended greenlets' frame chains.
- An optional private-stack **fiber core** (`fil_fiber.hpp` and hooks in
  `TGreenlet.hpp`/`TStackState.cpp`/`TUserGreenlet.cpp`), replacing
  stack-slicing with per-greenlet mmap'd stacks on CPython 3.10+
  (aarch64/x86_64, GIL builds); build-time selectable via `FIL_FIBER_CORE`.
- Performance patches that have been proposed upstream (see the project's
  `upstream/` patch series): skipping the GC toggle in `may_switch_away()`
  when the top frame object exists, retaining the stack-copy buffer at
  high-water capacity, and a `GreenletChecker` exact-type fast path.
- Renamed module/capsule identity (`_fil_greenlet`, `_fil_greenlet._C_API`)
  and an ImportError stub on interpreters where the vendored copy is not
  used (< 3.10), falling back to an installed classic greenlet.
