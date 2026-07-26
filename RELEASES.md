# Release notes

## 0.9.2 (2026-07-26)

**Bug fixes**

- Fixed the greenthread leak that made every compat shim grow without bound.
  `Filament` and `Message` inherited a `tp_traverse` that did not visit the
  fields they add, so the reference cycle every shim creates was invisible to
  the collector rather than merely uncollected. A `gevent_compat` WSGI server
  went from 1618 bytes of RSS growth per connection to ~0.
- Fixed a second leak of greenlet's internal C++ object, ~224 bytes per
  greenthread, with no Python object to account for it: dealloc called
  `tp_free` directly instead of chaining to greenlet's `tp_dealloc`.
- Fixed an unconditional hang at interpreter exit for any program that used a
  thread pool without shutting it down explicitly, including
  `filament.tpool.execute()`. Live pools are now shut down from `atexit`,
  before finalization makes the shutdown impossible to complete.
- Fixed `os.read()` / `os.write()` on regular files raising
  `RuntimeError: Couldn't add event` under monkey-patching, which broke
  anything writing to a file -- `tempfile` included, so a WSGI app whose
  framework spilled a large request body to disk returned a 500.
- `Group` and `Pool` no longer retain every greenthread they have ever
  spawned, which made `StreamServer(spawn=<int>)` accumulate every connection
  it had ever served.
- Python 2.7: importing any `_filament` submodule before the `filament`
  package raised `AttributeError: 'module' object has no attribute 'core'`.

**Performance**

- `gevent.joinall()` / `wait()` / `iwait()` no longer park a watcher
  greenthread on every object just to observe that it finished, which doubled
  the greenthread count of every scatter-gather. A 20-way fan-out over HTTP
  went from 2386 to 5392 rps (real gevent: 4136).
- Greenthread spawn is 10-25% slower than 0.9.1 on spawn-heavy microbenchmarks.
  That is the cost of the leak fixes above actually freeing what they allocate;
  filament still spawns 1.7-4.5x faster than gevent. Every other benchmark is
  unchanged.

**Testing**

- New `tests/test_leaks.py` covering the leaks and the interpreter-exit hangs,
  plus `iwait` and regular-file I/O coverage.

## 0.9.1 (2026-07-26)

**Bug fixes**

- Fixed heap corruption when deallocating instances of Python subclasses of
  the C types (e.g. `thread.LockType`, `threading.BoundedSemaphore`, the DNS
  `Resolver`): every C dealloc freed with a bare `PyObject_Del` instead of
  `tp_free`, corrupting the allocator and causing nondeterministic segfaults
  later.
- Fixed an intermittent abort at interpreter exit on Python 3.14
  (`gilstate_tss_set: failed to set current tstate`): the default DNS
  resolver's worker threads were never shut down and raced interpreter
  finalization. The resolver now joins its workers via `atexit`,
  `tpool.shutdown()` actually waits for its workers, and pool worker
  teardown skips thread-state re-attachment once finalization has begun.
- `threading.Timer` now fires; `Event.wait(timeout)` returns `False` and
  `Thread.join(timeout)` returns quietly on expiry (stdlib contracts),
  instead of leaking the internal timeout exception.
- Resolver lookups with keyword arguments (e.g.
  `getaddrinfo(host, port, type=...)`) no longer raise `TypeError`.
- `os_read`/`os_write`/`fd_wait_*_ready` on a negative fd raise
  `OSError(EBADF)` instead of parking forever.
- Python 2.7 repairs: missing `absolute_import` broke `filament.os.fdopen`
  entirely (and silently mis-bound modules in `pyqueue`/`subprocess`), and
  the cooperative `call`/`check_call`/`check_output`/`run` wrappers now work
  on 2.7 (context-manager `Popen`, `.args`, `TimeoutExpired`/
  `CompletedProcess` stand-ins).

**Internals**

- Removed ~450 lines of dead C: the unreachable processor-based socket-op
  API in the io thread and unused lock C-API wrappers.

**Testing & CI**

- Test suite grew from 233 to 442 tests (green on 2.7 and 3.8–3.15), with
  coverage measured properly for both halves: ~97% line coverage for the
  Python package, 74% lines / 98% functions for the C extensions.
- CI uploads Python and C coverage to Codecov under separate flags; README
  carries CI and per-language coverage badges.

## 0.9.0 (2026-07-26)

First release. Thirteen years after the proof of concept, filament is a
complete, fast, drop-in alternative to gevent and eventlet (the origin story
is below).

**Core**

- C scheduler with lightweight greenthreads: spawn/join, `sleep`, timers,
  events, and message-passing, all implemented in C (`_filament.*` extension
  modules).
- Per-OS-thread schedulers with safe cross-thread synchronization: switches
  requested from a foreign thread are deferred to the greenlet's home
  scheduler (the design that fixes eventlet **#137**). Semaphores, locks,
  conditions, and queues work between greenthreads *and* native threads —
  a single bounded `Queue` can be shared by greenthread and
  `threading.Thread` producers/consumers simultaneously.
- C `Queue`/`SimpleQueue`, locking primitives, timers, a libevent-backed
  I/O thread with edge-triggered persistent readiness events, cooperative
  sockets, and a real-OS-thread pool (`tpool`) with MRU worker wakeup.
- Cross-thread wakeup is pure futex/condvar — no file-descriptor round-trip.

**Compatibility**

- Drop-in **gevent** and **eventlet** compatibility shims
  (`filament.gevent_compat`, `filament.eventlet_compat`) covering the
  greenlet/Greenlet lifecycle, Group/Pool, Timeout, AsyncResult/Event/Waiter,
  Channel, queue family, StreamServer/pywsgi, and ThreadPool semantics.
- Cooperative stdlib replacements: `socket`, `ssl`, `select`, `time`,
  `threading`, `thread`, `subprocess`, `os`, `queue`, plus a monkey-patcher.

**Vendored greenlet (Python 3)**

- Ships its own greenlet runtime (`_fil_greenlet`, based on greenlet 3.5.4)
  with a private capsule — no conflict with an installed greenlet — a C
  fast-switch entry, and ported upstream performance patches.
- Optional private-stack **fiber core** (mmap'd stacks + asm switch), the
  default where the interpreter supports it; classic stack-slicing core
  everywhere else. Python 2.7 transparently uses stock greenlet.
- Runtime-selectable debug introspection (`filament.set_debug(True)` /
  `FILAMENT_DEBUG=1`, auto-armed under trace/profile hooks); frame exposure
  stays off the hot path by default.

**Performance**

- Faster than gevent and eventlet across the full benchmark matrix — spawn,
  context switch, semaphores, queues, tpool round-trips, and echo servers —
  on every tested interpreter and both architectures. See
  `benchmarks/RESULTS.md`.

**Platforms & packaging**

- Python 3.8–3.15 on Linux (amd64/arm64); Python 2.7 still builds and passes
  the suite (test-only, no published wheels).
- PEP 517/621 build; manylinux wheels for cp38–cp314 and a verified sdist
  attached to GitHub Releases on tag.
- OS packaging: RPM spec (`packaging/rpm/`) and Debian packaging (`debian/`),
  each building `python3-filament` for the system Python at build time.
