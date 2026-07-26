# Release history

## Unreleased

**Bug fixes**

- Fixed the greenthread leak that made every compat shim grow without bound:
  `Filament` and `Message` inherited a `tp_traverse` that did not visit the
  fields they add, so the wrapper/filament cycle every shim creates was
  invisible to the collector rather than merely uncollected. Both now have
  real `tp_traverse`/`tp_clear`, and a filament drops its callable as soon as
  the body returns so the cycle is broken by refcount. A `gevent_compat` WSGI
  server went from 1618 bytes of RSS growth per connection to ~0.
- Fixed a leak of greenlet's internal C++ object, ~224 bytes per greenthread
  created with no Python object to account for it: `_fil_filament_dealloc`
  called `tp_free` directly instead of chaining to greenlet's `tp_dealloc`.
- Fixed an unconditional hang at interpreter exit for any program that used a
  thread pool without shutting it down explicitly (including
  `filament.tpool.execute()`), reproducible whenever stdout was a pipe. The
  implicit shutdown in `tp_dealloc` cannot complete once finalization has
  begun; live pools are now shut down from `atexit` instead.
- `Group` and `Pool` no longer retain every greenthread they have ever
  spawned, which made `StreamServer(spawn=<int>)` accumulate every connection
  it had ever served. They untrack each one as it finishes, matching gevent.
- `gevent.joinall()` / `wait()` / `iwait()` no longer park a watcher
  greenthread on every object just to observe that it finished, which doubled
  the greenthread count of every scatter-gather. A 20-way fan-out over HTTP
  went from 2386 to 5392 rps (real gevent: 4136).
- Python 2.7: importing any `_filament` submodule before the `filament`
  package raised `AttributeError: 'module' object has no attribute 'core'`,
  because `_filament.core`'s init re-enters itself through `filament.exc`.

**Testing**

- New `tests/test_leaks.py` covering the leaks and the interpreter-exit hangs,
  plus `iwait` coverage in `tests/test_gevent_compat_more.py`.

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

---

# How filament began

In early 2013 while I was working on OpenStack, which used eventlet, we
discovered an occasional hang and sometimes some traceback spew from eventlet
about not being able to switch greenlets. I triaged it enough to determine
it happened while using eventlet's tpool (real OS threads pool) in combination
with calls to the logging library in python. The logging library makes use of
a threading lock, which eventlet had monkey patched.

I filed the issue (as **#137**) in eventlet's Bitbucket tracker — its home at
the time — on **February 17, 2013**, and tracked it down to eventlet's `Semaphore`
class not being safe across OS threads. Python's `logging` module guards its
handlers with a lock, and eventlet's monkey-patching converts that lock into
a `Semaphore` — so the net result was that you could not log from inside
a tpool (OS) thread without risking a deadlock. A workaround was available,
which was to monkey patch with thread=False. This avoided the patching of the
logging lock. On **February 18, 2013** I filed the bug with OpenStack Compute
(Nova), here: [bug #1128684](https://bugs.launchpad.net/nova/+bug/1128684),
noting the issue, citing the eventlet bitbucket issue number, and the
workaround.

I came up with a potential fix and submitted an initial pull request against
eventlet on **February 19**. It didn't fully fix the problem, and the more I
dug, the clearer it became that a *proper* fix inside eventlet would kill its
performance without rewriting `Semaphore` in C. I recall looking at gevent and
determining the same issue was there. OpenStack ended up simply working around
the bug — more than once — by avoiding logging inside tpools.

Meanwhile, I thought it would be fun to take a stab at the Semaphore in C and
that led to re-imagining the whole core in C. The main fix was to defer
greenlet switching to the greenlet's home scheduler in the OS thread where the
greenlet was started, vs a foreign thread trying to directly switch back to a
greenlet in another thread.

Filament was born. Tests showed a ~10x improvement against eventlet for
greenthread spawns at the time. Some work was done to implement queues and
a few other things. And then it sat in this mostly-working proof of concept
stage for 13 years. I moved on to other things and did not find the time to
finish it. Finally, with help from AI, it's a fully working project: a drop-in
replacement for both eventlet and gevent, with full support for synchronization
and queues between greenlets in multiple OS threads.

And it performs.

---

*Postscript: the same underlying bug was later re-reported against eventlet in
the GitHub era as issue #432 ("Semaphore does not work across different hubs
in different pthreads"), and it still bites gevent and eventlet today —
filament's test suite carries a regression test
(`tests/test_cross_thread_137.py`) that logs from thread-pool workers while
the hub runs greenthreads, and the benchmark suite runs the same workload
against all three libraries. Filament completes it; the other two deadlock.
See [README.md](README.md) for more about the design that makes this work.*
