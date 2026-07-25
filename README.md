# Filament

Filament is a greenlet-based cooperative concurrency library for Python — an
efficient alternative to [gevent](http://www.gevent.org/) and
[eventlet](https://eventlet.readthedocs.io/) built around a small C core. It
gives you lightweight "greenthreads" that yield to a scheduler on I/O and
synchronization instead of blocking OS threads, plus cooperative drop-in
replacements for the standard library (`socket`, `ssl`, `select`, `time`,
`os`, `subprocess`, `threading`, `queue`) and near drop-in compatibility shims
for both `gevent` and `eventlet`.

It runs on **CPython 2.7 and 3.8–3.15** (the same source, one set of C
extensions), and it does **not** have the cross-thread greenlet-switch bug
that still bites gevent and eventlet today (see below).

## How filament began

Filament grew out of a bug that was hit in 2013 in the OpenStack project, which was
built on eventlet: logging from inside a thread pool could deadlock the whole
process, because eventlet's monkey-patched locks aren't safe across OS
threads. A real fix inside eventlet would have destroyed its performance — but
moving the right pieces into C made it possible to fix the design *and* keep
(even improve) the speed. The full story is in [HISTORY.md](HISTORY.md).

## Design: no cross-thread switches

greenlet binds every greenlet to the OS thread that first switched into it;
switching into it from another thread raises
`greenlet.error: cannot switch to a different thread`. gevent and eventlet
each run a per-thread "hub", but their synchronization primitives can end up
trying to wake a greenlet that lives on a *different* thread. The classic
trigger is the standard `logging` module: it holds a module-level mutex, and
once that mutex has been monkey-patched into a green lock, logging from inside
a real OS-thread pool while the hub runs greenthreads on the main thread
deadlocks (or crashes with the error above).

Filament's answer is structural:

- **One scheduler per OS thread**, stored in thread-specific data. A scheduler
  is itself a greenlet running an event loop with the GIL released.
- **Waiters bind (scheduler, greenlet) together** at the moment they wait.
- **Signalling never switches a greenlet across threads.** Waking a waiter
  only *enqueues* the switch onto that greenlet's home scheduler and pokes its
  condition variable; the actual `greenlet.switch()` happens later, on the
  owning thread, from that scheduler's own loop.

So a wakeup originating in the I/O thread, a thread-pool worker, or any other
OS thread is always *deferred into the correct thread* — there is no
cross-thread switch to get wrong. Logging from a thread pool "just works"
(in the included benchmark, filament runs that workload at ~15–17k msg/s while
both gevent and eventlet deadlock — even with gevent's documented
mitigations), and a single filament `Queue` can be shared freely between
greenthreads and native `threading.Thread` workers — a pattern that is
undefined behavior on gevent and eventlet.

## Installation

Requirements:

- Python 2.7 or 3.8–3.15
- [greenlet](https://pypi.org/project/greenlet/) (use the 1.1.x line for
  Python 2.7, 3.x otherwise). On Python 3.10+ filament builds and prefers its
  own vendored, performance-tuned greenlet fork (`_fil_greenlet`) — the
  installed greenlet is still used for headers at build time and as a runtime
  fallback on 2.7/3.8/3.9.
- A C compiler (C++ for the vendored greenlet) and `libevent` development
  headers (Debian/Ubuntu: `sudo apt-get install libevent-dev`).
  `libbluetooth-dev` is optional (Bluetooth socket support; compiled in only
  when the header is present).

Build the extensions in place:

```sh
pip install greenlet
python setup.py build_ext --inplace
```

Filament ships **seven** C extension modules under the `_filament` package
(`core`, `io`, `socket`, `queue`, `locking`, `timer`, `thrpool`), plus the
vendored greenlet extension on 3.10+; the user-facing API is the pure-Python
`filament` package layered on top.

On Python 3.10+ the vendored greenlet uses a **private-stack fiber core** by
default (each greenthread gets its own guard-paged stack; switches are a
minimal assembly path — no per-switch stack copying). Set `FIL_FIBER_CORE=0`
at build time to keep classic greenlet stack-slicing instead; tune
`FIL_FIBER_STACK_SIZE` / `FIL_FIBER_POOL_MAX` at runtime for unusual
concurrency/memory profiles.

## Quick start

Native API:

```python
import filament

def worker(n):
    filament.sleep(0.01)
    return n * n

# spawn returns a greenthread; .wait() joins it and returns the value
# (or re-raises the exception raised inside it).
gts = [filament.spawn(worker, i) for i in range(1000)]
results = [gt.wait() for gt in gts]

# Pools bound concurrency:
pool = filament.GreenPool(50)
for i in range(1000):
    pool.spawn(worker, i)
pool.waitall()

# Events, results, timeouts:
ev = filament.Event()
ar = filament.AsyncResult()
with filament.Timeout(5.0):
    ...

# Run a blocking call in a real OS-thread pool without blocking the hub:
filament.tpool.execute(some_blocking_function, arg)
```

Cooperative sockets:

```python
from filament import socket

srv = socket.socket()
srv.bind(("127.0.0.1", 0)); srv.listen(128)

def serve():
    while True:
        conn, _ = srv.accept()
        filament.spawn(handle, conn)   # one greenthread per connection
```

## Monkey-patching

Make the standard library cooperative (like `gevent.monkey` /
`eventlet.monkey_patch`):

```python
import filament.patcher
filament.patcher.patch_all()          # socket, ssl, select, os, time,
                                      # thread, threading, subprocess, queue
```

Granular patches are available too (`patch_socket`, `patch_ssl`,
`patch_select`, `patch_os`, `patch_time`, `patch_thread`, `patch_subprocess`,
`patch_queue`), plus `get_original`, `is_module_patched`, and
`is_object_patched`.

`patch_thread(logging=True, existing_locks=True)` converts the already-created
`logging` locks (the module lock and every handler lock) to cooperative locks
while the process is still single-threaded — this, together with the scheduler
design above, is what keeps logging-from-a-thread-pool safe.

## Drop-in gevent / eventlet

Filament can masquerade as `gevent` or `eventlet` without shadowing the real
packages on disk. Install the shim *before* importing under the target name:

```python
import filament.gevent_compat as gevent_compat
gevent_compat.install()               # registers sys.modules['gevent'], etc.

import gevent
from gevent import monkey; monkey.patch_all()
from gevent.pool import Pool
from gevent.pywsgi import WSGIServer
```

...and similarly:

```python
import filament.eventlet_compat as eventlet_compat
eventlet_compat.install()

import eventlet
from eventlet.green import socket
pool = eventlet.GreenPool()
```

The shims cover the common surface: `spawn`/`spawn_n`/`spawn_later`,
`Greenlet`/`GreenThread`, `joinall`/`killall`, `Event`/`AsyncResult`,
`Timeout`/`with_timeout`, `Semaphore`/`lock`, `Pool`/`Group`/`GreenPool`/
`GreenPile`, `queue` (incl. `Channel`), `monkey`/`monkey_patch`, the
`green.*` / `gevent.socket` etc. modules, `tpool`/`threadpool`,
`hubs.trampoline`, a working `StreamServer` and a minimal `pywsgi`/`wsgi` WSGI
server. See the module docstrings for the handful of documented stubs.

## Feature parity

Implemented, mapped onto filament's C core and native primitives:

- **Greenthreads:** `spawn`, `spawn_n`, `spawn_later`/`spawn_after`,
  `kill`/`killall`, `joinall`, `wait`/`iwait`, `getcurrent`, `sleep`,
  `yield_thread`.
- **Sync/result:** `Event`, `AsyncResult`, `Lock`, `RLock`, `Condition`,
  `Semaphore`, `Timeout`/`with_timeout`.
- **Pools:** `Group`, `Pool`, `GreenPool`, `GreenPile`.
- **Queues:** `Queue`, `SimpleQueue` (C), plus pure-Python
  `PriorityQueue`/`LifoQueue` and gevent's `Channel`. Filament queues are
  safe to share between greenthreads and native OS threads simultaneously.
- **Native-thread offload:** `tpool.execute` / `tpool.Proxy` (and a
  gevent-shaped `ThreadPool`).
- **Cooperative stdlib:** `socket`, `ssl` (modern `SSLContext`), `select`
  (`select()`; `poll` raises a clear error), `time`, `os` (`read`/`write`),
  `subprocess` (cooperative `wait`/`communicate`), `threading` (cooperative
  `Thread`, greenlet-local `local`), `queue`.
- **Servers:** `StreamServer` and a minimal WSGI server via the compat shims.

## Debugging

By default (on 3.12+), switches skip eagerly materializing frame state and
`gr_frame`-style introspection is reconstructed lazily on access — tracebacks
and postmortems always work. For live debugging, `filament.set_debug(True)`
(or `FILAMENT_DEBUG=1`, or simply installing a trace/profile hook — it
auto-arms) restores fully eager frame exposure, at a small per-switch cost.

## Python version support

The same source builds and passes the full test suite on **CPython 2.7.18,
3.8, 3.10, 3.11, 3.12, 3.13, 3.14, and 3.15 (beta)** — 220 tests on 3.12/3.13,
214 (+6 lazy-debug-only skips) elsewhere. Python 3.9 is expected to work via
the classic-greenlet fallback but is not in the tested matrix. Python 2 vs 3
differences are centralized in `include/core/pyversion.h` (string/int APIs,
module init, greenlet parent-reference ownership) rather than scattered
through the C.

## Benchmarks

`benchmarks/` contains a filament-vs-gevent-vs-eventlet suite (spawn
throughput, context-switch rate, semaphore/queue ops, a mixed
greenthread+native-thread shared queue, thread-pool round-trip, echo-server
req/s + latency, and the #137 logging test), each framework run in a fresh
subprocess. Run it with:

```sh
python benchmarks/run_all.py [--python /path/to/venv/bin/python]
```

Full numbers are in [benchmarks/RESULTS.md](benchmarks/RESULTS.md). As of the
latest full matrix, **filament wins or ties every benchmark against both
gevent and eventlet on every supported interpreter** (2.7 through 3.15).
Headlines (within-version ratios vs gevent):

- **Context switches:** 2.4–3.1× (3.4–4.4M switches/s with the fiber core).
- **Spawn throughput:** ~2.4× on 3.13, widening to ~6× on 2.7.
- **Semaphore ops:** ~4×; **queue:** ~1.3×.
- **Thread-pool round-trip and echo-server req/s:** ahead of gevent, with
  substantially better p99 tail latency at high concurrency.
- **Mixed green+native shared queue:** ~1–3M items/s; gevent silently loses
  items and eventlet deadlocks on the same workload.
- **#137 logging-from-threadpool:** filament completes (~15–17k msg/s); gevent
  and eventlet both **deadlock**.

## Running the tests

```sh
python -m pytest tests/
```

The suite covers the native API, the cooperative stdlib, the patcher, both
compat shims, cross-thread queue sharing, and the runtime debug modes.
`tests/test_cross_thread_137.py` is the regression test for the bug described
above: it logs from thread-pool workers while the hub runs greenthreads and
asserts there is no `greenlet.error` and no deadlock.

## License

MIT. Copyright (c) 2013–2026, Chris Behrens. See `LICENSE`.

`vendor/greenlet/` contains a vendored copy of
[greenlet](https://github.com/python-greenlet/greenlet) 3.5.4 with
filament-specific modifications; it remains under greenlet's own licenses
(MIT-style, plus the PSF license for its Stackless-derived platform files) —
see `vendor/greenlet/LICENSE`, `vendor/greenlet/LICENSE.PSF`, and
`vendor/greenlet/VENDORED.md` for provenance and the list of local changes.
A few small portions of filament itself are derived from CPython's standard
library under the PSF License (`LICENSE.PSF`); see `THIRD_PARTY_NOTICES.md`
for the complete list.
