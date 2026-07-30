# Release notes

## Unreleased

**Performance**

- The io thread performs a blocked socket `recv()` into the waiting
  greenthread's own buffer before waking it, and issues a blocked `send()` from
  the caller's buffer the same way. The wakeup hands back data rather than mere
  readiness, and the woken side does not re-enter the kernel at all.

  This restores something the original design had and the edge-triggered work
  quietly dropped: the kernel-to-user copy used to happen on the io thread with
  the GIL released, while Python carried on running whatever greenthreads were
  already scheduled. When the cached edge-triggered path went in it went in as a
  *readiness* signal and the syscall moved back onto the calling thread as a
  side effect. The cached path keeps its cheap persistent event; it just
  delivers bytes again.

  The syscall count does not change -- *which thread pays* does. On the calling
  thread every `recv` is bracketed by a GIL drop and reacquire, and that thread
  is the saturated one. Echo gains **+9.7% to +88%** depending on payload and
  concurrency, with p99 7-47% better. That is the ceiling, not the expectation:
  the win scales with how thin the Python layer above the socket is, so a locust
  `FastPingUser` @1000 users sees **+11.6%** (1.18x -> 1.32x gevent) and a bottle
  WSGI handler sees nothing measurable. Eager `send` is a wash on loopback --
  the kernel autotunes `SO_SNDBUF` to megabytes there, so it never arms -- and
  worth **+33%** on bulk transfer (8 GB in 4 MB blocks, 9.4 -> 12.5 GB/s, main
  thread CPU per MB -25%).

  Nothing is speculatively read and nothing is buffered. The io thread issues
  exactly the call the parked greenthread asked for, with its length and flags,
  into its buffer -- so `recv_into` keeps its single copy, `MSG_PEEK` still does
  not consume, and TCP backpressure stays in the kernel. If the io thread cannot
  complete the call it reports nothing and the caller retries as before.
  `accept`, `recvfrom` and all TLS are untouched.

  This corrects the last paragraph of the 0.9.4 note below: handing the syscall
  to the io thread is not a handoff, because it already has the edge and already
  does the wakeup. The earlier comparison only looked otherwise because the one
  configuration that then read on the io thread was the classic path, which also
  pays ~3 us of per-operation registration churn.

## 0.9.4 (2026-07-30)

**Packaging**

- Dropped Python 3.8 (end-of-life October 2024); the floor is now 3.9, and 3.9
  and 3.15 are both in the tested matrix for the first time. This is what makes
  the license metadata modern: `project.license` is now a PEP 639 SPDX string
  with `project.license-files` alongside it, which needs `setuptools>=77` --
  unreachable on 3.8, where setuptools stops at 75.x and rejects the string form
  outright rather than warning. Both `SetuptoolsDeprecationWarning`s at build
  time are gone.
- `setup.py` finds libevent on macOS, where Homebrew installs outside the
  compiler's default search path: `LIBEVENT_PREFIX`, then `pkg-config`, then
  `brew --prefix`, then the standard Homebrew prefixes. Linux is unaffected --
  the distro package is already on the search path, so nothing is added.
- The vendored greenlet's fiber-switch assembly assembles on Mach-O. It used
  ELF-only directives (`.hidden`, `.type`, `.size`) that Apple's assembler
  rejects, and Mach-O also prefixes C symbols with an underscore -- so the
  symbol would not have linked even once the directives were accepted.
- The Debian and RPM packaging now ask for `setuptools >= 77` rather than 64,
  matching the floor the license metadata above needs. Below it the build fails
  in a way that never mentions setuptools -- a `project.license` `ValueError`
  out of `pyproject.toml` parsing.
- `src/` and the vendored greenlet compile warning-free under `-Wall
  -Wsign-compare`, with gcc and with clang, under either switching core. Four
  warnings went: a `size_t` resolver-method count compared against signed
  indices, and -- surfaced by clang on macOS -- an assert-only variable and the
  classic `slp_switch()`, which the private-stack fiber core defines but never
  calls.

**Performance**

- A socket with `settimeout()` set now uses the same cheap cached
  edge-triggered wait as one without. It used to be pushed onto the classic io
  path, which pays -- for *every* blocked operation -- two `epoll_ctl`
  syscalls, an `event_new`/`event_free`, two mutex/cond init+destroy pairs and
  a malloc, and has the io thread perform the `recv`/`send` itself under a
  lock. Since connection pools set a timeout on every pooled connection
  (`urllib3` and `geventhttpclient` both do), client-side workloads were
  paying that on essentially every request: measured across 50 to 4000
  connections, setting a timeout alone took filament from ~1.6x *faster* than
  gevent to ~0.98x on an otherwise identical workload.

  The deadline never goes near the io thread. The persistent event stays a
  pure untimed readiness signal and the deadline is armed on the waiting
  greenthread's own scheduler timer, so the io thread's wakeup remains the
  GIL-free one it always was. `epoll_ctl` per blocked operation drops from
  ~4.6 to ~0.002; a `geventhttpclient` load-generation benchmark goes from 8%
  behind gevent to 21% ahead at 100 connections and 26% ahead at 1000, with
  roughly half the p95 latency. Workloads that never set a timeout are
  unaffected.

  Worth recording what this is *not*: the io thread was never the bottleneck.
  Across the same sweep it never exceeded 0.47 of one core, and without a
  timeout its cost per request *falls* as connection count rises. Neither
  additional io threads nor handing the read/write syscalls to a worker pool
  would have helped.

**Bug fixes**

- Fixed `sendall()` blocking forever on a socket with `settimeout()` set. The
  deadline was computed lazily and shared across the call's segments by
  passing a null buffer to mean "already computed" -- but a first segment that
  *partially* succeeded returned before computing one, so every later segment
  ran with no deadline at all and the call could never time out. The
  "computed" state is now tracked explicitly rather than encoded in a pointer,
  which also lets every segment take the fast path instead of only the first.

- The internal wakeup contract no longer claims that a caller without the GIL
  may only wake an *untimed* waiter. That was true when timed waiters were
  resumed through a path that touched a refcount; they are not any more, and
  the restriction it documented had already stopped existing. Nothing depended
  on it, but the timeout work above would have looked unsafe against it.

**Internals**

- `FIL_SCHED_EVENT_FREELIST_MAX` was defined twice with *different* values --
  256 in `include/core/fil_scheduler.h`, 2048 in `src/core/fil_scheduler.c`.
  The scheduler saw 2048 because its own definition came after the include, so
  behaviour was right, but any other translation unit including the header got
  256. Consolidated on the documented 2048.

**Testing & benchmarks**

- The benchmark matrix was re-measured end to end on both architectures, on
  bare metal this time rather than in a VM. Python 3.9 is included and the 3.8
  rows are gone.
- The benchmark harness can tell a slow host from a hung one. It used to cap
  each case with a fixed wall-clock timeout, which killed legitimately slow
  runs on a loaded or slower machine; it now times out on *idle* output
  instead, and long cases emit progress while they work.
- The harness no longer leaks workers. A case that timed out left its worker
  process group running -- a spinning gevent worker survived at a full core
  and skewed everything measured afterwards. Process groups are now tracked
  and reaped on exit, including on Ctrl-C or SIGTERM.

## 0.9.3 (2026-07-28)

Most of this came out of swapping `gevent_compat` in for gevent under a
large gevent-native application -- running both its test suite and its load
generator against the shim.

**gevent compatibility**

- `patch_all()` no longer breaks `queue.LifoQueue` / `queue.PriorityQueue`:
  the green `queue` module only carried `Queue` and `SimpleQueue`, so patched
  code that used the others (urllib3's connection pool, for one) died with an
  `AttributeError` on import.
- `filament.select` gained a cooperative `poll()` object, which `urllib3`
  reaches for on every reused connection -- so `requests` over a pooled
  connection failed outright. `select()` itself no longer waits out the full
  timeout when it has more than one descriptor and one of them is ready, and
  `timeout=0` now means "poll" instead of "expire immediately".
- `gevent.hub`'s loop grew real `io()` watchers, which is what `zmq.green`
  builds its whole gevent integration on; without them `import zmq.green`
  fell through to a gevent<1.0 path and raised.
- `gevent.pywsgi` now speaks HTTP/1.1 keep-alive, with gevent's framing rules
  (the app's `Content-Length` is honoured, an HTTP/1.1 response without one is
  chunked, anything else is close-delimited), decodes chunked request bodies,
  drains a body the app did not read, and exposes the `handle()` /
  `log_request()` / `format_request()` hooks that handler subclasses override.
  `StreamServer` gained `start_accepting()` / `stop_accepting()`.
- New API surface that real projects use and we did not have:
  `gevent.signal_handler()`, `Timeout.close()`, `gevent.greenlet` and
  `gevent.timeout` as importable modules, `monkey.MonkeyPatchWarning`, and
  `Greenlet.args` / `.kwargs` / `.exc_info` / `.name` / `.minimal_ident`.
- `patch_dns()` installs the `filament.socket` resolver functions rather than
  the raw C ones, so a patched `getaddrinfo()` returns `AddressFamily` /
  `SocketKind` enums like the stdlib instead of bare ints. Code does read
  those back -- IPv6 support is commonly detected by looking for
  `Family.AF_INET6` in the repr.
- `gevent.pywsgi` tolerates a blank line before a request line instead of
  dropping the connection. RFC 7230 says a server should ignore at least one,
  gevent's pywsgi does, and clients do emit a stray CRLF after a request
  body -- so a keep-alive session gevent would have continued was ending
  early.
- `install()` now also owns the top-level `greenlet` name, so
  `greenlet.getcurrent()` returns the running `gevent.Greenlet` the way it
  does under real gevent (where `Greenlet` *is* a `greenlet.greenlet`
  subclass).
  `gevent.getcurrent` is the same function, as it is in gevent. The real
  greenlet package is never mutated and `uninstall()` puts it back.
- A callback registered with `link_exception()` / `link_value()` / `link()`
  before `join()` has now run by the time `join()` returns, matching gevent
  (whose `join()` *is* a link on the same ordered list). The shim woke
  joiners first and merely queued the links, so anything that logs unhandled
  greenlet exceptions through a link saw nothing.
  `AsyncResult.set()` / `set_exception()` had the same inversion.

**Bug fixes**

- Fixed a wait with no timeout reporting a timeout -- `Queue.get()` raising
  `Empty`, a lock acquire failing, and so on. A `sleep()` cut short by
  anything other than its own deadline (an expiring `Timeout`, a `kill()`)
  left the wakeup it had queued for itself in the scheduler, and that wakeup
  later fired into whatever the greenthread had moved on to; a wait resumed
  that way saw no signal, no exception, and concluded it must have timed out.
  Timed `sleep()` now parks on a waiter, so its wakeup is cancelled when
  something else resumes it first, and a wait that is resumed without being
  signalled goes back to waiting instead of fabricating a timeout.
- Fixed a use-after-free that segfaulted the scheduler when a greenthread was
  killed (or timed out) while a wakeup was already queued for it. Waking a
  parked greenthread queues a scheduler event that switches into it, and that
  event carried a *borrowed* pointer to the greenlet -- safe only while a
  parked greenlet resumes exclusively via that event, which a throw does not
  respect. The event now carries the waiter, together with a reference
  reserved before parking (so the off-GIL io thread still never touches a
  refcount), and a greenthread that resumes any other way cancels the queued
  wakeup. The same staleness could also resume a greenthread in the middle of
  a *later*, unrelated wait, which showed up as an untimed `Queue.get()`
  raising `Empty`.
- Fixed a leak of one libevent `struct event` (~144 bytes) per blocking io
  operation. `_iothread_process()` created an event for every call that had to
  park in the io thread and never freed it -- the io callback only
  `event_del()`s, which unregisters but does not release. The classic path it
  sits on is taken by `connect()`, by `recv`/`send` on a socket with a timeout
  set, by `os.read`/`os.write` and by `select`/`poll`, so an HTTP client (which
  sets a timeout) leaked on every request: a sustained load test grew
  ~2 MB/s where gevent was flat, and now holds steady slightly below it. The same
  function also skipped `fil_waiter_decref()` when the parked greenthread was
  thrown into, leaking a 136-byte waiter per cancelled wait.
- A timed wait that is satisfied before its deadline now cancels its timeout
  event instead of leaving it queued -- `Queue.get(timeout=60)` and friends
  used to pin an event, and a reference to the waiter, for the full 60
  seconds after returning in a millisecond.
- Fixed `SystemError: <method 'recv' of '_filament.socket.Socket' objects>
  returned a result with an exception set`, raised whenever a greenthread
  parked in `recv`/`send` was killed (or timed out) in the same wakeup that
  made the descriptor ready: the byte count came back with the exception
  still pending. The exception now wins and the bytes are dropped, as they
  would be if the throw had landed a moment earlier. This fired on every
  shutdown of a load test, taking the run's results with it.
- Fixed `SystemError: <built-in method __enter__ of _filament.locking.RLock
  object> returned a result with an exception set` at interpreter exit, which
  every run under a patched `logging` printed a pair of. `fil_get_ident()`
  asks greenlet for the current greenlet to build an RLock ownership id, and
  that call *fails* once the interpreter is tearing down
  (`RuntimeError: greenlet is being finalized`). The exception was left
  pending, so the acquire that followed -- which succeeded -- handed a result
  back to CPython with an error set. Any lock use from a `__del__` or a
  weakref callback at shutdown hit it; a fifteen-line script with no
  monkey-patching reproduces it.
- A greenthread that is handed a lock, a semaphore permit, a queue item or a
  thread-pool result and is *thrown into in the same wakeup* no longer walks
  off with it. `release()` transfers ownership straight to a parked waiter,
  and if a `kill()` or an expiring `Timeout` landed in the same scheduler
  pass, `acquire()` returned success with the exception still pending: another
  `SystemError`, and worse, a lock left permanently held by a greenthread that
  no longer existed. `fil_waiter_wait()` now reports that case distinctly
  (`FIL_WAITER_SIGNALED_UNWIND`) and every primitive passes the hand-over on
  -- to the next waiter, or back to itself -- before letting the exception
  out. The thread-pool paths were leaking or writing to freed memory on the
  same race.
- Fixed an assertion in the scheduler's deallocator that aborted any build
  with assertions enabled. It required the *running* thread to have no
  scheduler, but the last reference to a scheduler is usually dropped by the
  cycle collector, which runs on whatever thread happened to allocate --
  routinely another one with a scheduler of its own. It now asserts what was
  meant: that the scheduler is not still installed in its own thread's slot.
  The scheduler's exit path also cleared that slot only after dropping the
  reference it held, so a last-reference drop reached the deallocator with
  the slot still pointing at it.

**Scheduler**

- The scheduler's event queue is no longer a single sorted linked list.
  "Wake up now" events -- one per greenthread switch, the hottest path there
  is -- go on a FIFO that is O(1) to push and pop and keeps the relative order
  of `sleep(0)` yields. Timed events go in a binary min-heap keyed on the
  deadline, so arming a timeout is O(log n) instead of a linear walk. With
  1000 timers already armed, arming another went from 9.4us to 0.5us; with
  50000, from 19.0us to 0.5us.
- `Timeout.cancel()` (and `_filament.timer.Timer.cancel()`) now removes the
  event from the scheduler instead of just flagging it. A cancelled timeout
  used to occupy its slot until the deadline it was never going to reach, so
  code that arms a timeout per operation -- any HTTP client does -- grew the
  queue in proportion to *request rate x timeout*. 20000 armed-and-cancelled
  30-second timeouts retained 9.4 MB before and 0 now, and the cost of the
  next arm/cancel stopped scaling with the dead ones (23.6us -> 0.4us).
- Each scheduler pass now runs expired timers before the ready-now queue.
  Both sets still run in the same pass, so this costs the immediate wakeups
  nothing; it just stops timer callbacks from queueing behind a switch storm.
- New `Scheduler.queue_depth()` returns `(immediate_count, timer_count)` for
  diagnosing exactly this kind of thing.

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
