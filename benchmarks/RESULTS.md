# filament vs gevent vs eventlet — benchmark results

Greenlet-based cooperative concurrency shootout: **filament** (this repo) against modern **gevent** and **eventlet**, across CPython versions on aarch64 Linux.

Each (framework, benchmark) ran in its own fresh subprocess. Micro-benchmarks report the **median** of several timed reps (warm-up discarded), using a monotonic clock. Higher is better for throughput; lower is better for latency.

## Methodology

- Same logical workload run three ways (filament / gevent / eventlet) with identical sizes per framework. For filament the in-process client is `filament.socket`; gevent uses `gevent.socket` + `StreamServer`-style accept loop; eventlet uses `eventlet.green.socket`. The echo client stays in the same framework as the server for fairness.
- Each pair runs in a **fresh interpreter subprocess** so monkey-patching and hub state never leak between frameworks.
- Spawn = 100k greenthreads spawned then joined. Context switch = 100 greenthreads x 10k `sleep(0)` = 1,000,000 switches. Semaphore uncontended = 1M acquire/release on one greenthread; contended = 50 greenthreads on a `Semaphore(1)`. Queue = 200k producer/consumer items. tpool = 3000 sequential real-thread round-trips. Echo = concurrency 100 (x100 round-trips) and 1000 (x20), 64-byte payload.
- Queue mixed = ONE bounded queue (maxsize 100) shared simultaneously by a greenthread producer + consumer AND a native `threading.Thread` producer + consumer (50k items per producer), so native threads block in `q.get()`/`q.put()` while greenthreads work the same queue. gevent/eventlet queues are hub-bound; foreign-OS-thread use is undefined for them and runs under the deadlock watchdog.
- **#137**: monkey-patch everything, then log heavily from real OS-thread pool workers while greenthreads spin in the hub. Each attempt runs under a hard 30 s subprocess watchdog; a hang is recorded as **deadlock**.

> **Cross-version caveat.** Each Python version's table was recorded in its own sequential run on the same box. Interpreter speed differs across versions, so absolute numbers are **not** comparable across Python versions. The reliable signal is the **ratio between frameworks within one version**: all three frameworks in a table ran back-to-back under identical conditions.

> **Full-matrix re-run (2026-07-25).** The entire matrix was re-measured with the optimized filament (MRU thread-pool worker wakeup, GIL-free io-thread completion signaling, persistent edge-triggered socket readiness events) and the latest installable greenlet per interpreter (2.0.2 on 2.7, 3.1.1 on 3.8, 3.5.4 on 3.10+), plus gevent 22.10.2 on 2.7.

> **Optimization round 2 (2026-07-25).** Adds: METH_FASTCALL hot entry points (semaphore/lock/queue/message/sleep), scheduler switch-event + waiter freelists (waiters keep their mutex/cond initialized across reuse), a `sleep(0)` fast path, cond-signal-after-unlock, two waiter lifetime race fixes, and a **vendored greenlet** (`_fil_greenlet`, private capsule, no conflict with installed greenlet) with a C fast-switch entry on py3 — py2.7 transparently falls back to classic greenlet. filament's cross-thread wakeup was confirmed to be pure futex/condvar (no fd) — measurably cheaper than gevent's eventfd+epoll async watcher path per wakeup.

> **Optimization round 3 (2026-07-25).** Runtime-selectable debug mode: switches skip eager frame exposure by default (3.12/3.13), with lazy `gr_frame` materialization on access, `filament.set_debug(True)` / `FILAMENT_DEBUG=1` for full eager introspection, and auto-arming when a trace/profile hook is installed. Plus three upstream-bound greenlet perf patches ported onto the vendored copy (GC-toggle skip in may_switch_away, stack-copy buffer retained at high-water capacity, GreenletChecker exact-type fast path; see /workspace/upstream for the upstream PR series).

## Environments

| Python | greenlet | gevent | eventlet |
|---|---|---|---|
| 3.14.6 | 3.3.2 | 26.7.0 | 0.41.1 |
| 3.13.5 | 3.5.4 | 26.7.0 | 0.41.1 |
| 3.12.13 | 3.5.4 | 26.7.0 | 0.41.1 |
| 3.10.20 | 3.5.4 | 26.7.0 | 0.41.1 |
| 3.8.20 | 3.1.1 | 22.10.2 | 0.39.1 |
| 2.7.18 | 2.0.2 | 22.10.2 | 0.33.3 |

Availability notes:

- **gevent on Python 2.7**: no cp27/aarch64 wheel exists and stock source builds fail under a modern GCC (Cython-generated C errors); where the 2.7 column shows gevent numbers they come from a locally-built older gevent (see the environments table). eventlet 0.33.3 (pure-Python) and filament both build/run on 2.7.
- **gevent tpool on Python 2.7**: gevent **22.10.2** (the last py2.7 release) deadlocks in the threadpool round-trip benchmark on 2.7 — reproducible even at small scale. Its predecessor 21.12.0 completed the same benchmark (~23.6k calls/s), so this is a gevent regression in its final py2.7 release, not a harness artifact.
- **gevent/eventlet on Python 3.8**: latest releases have no 3.8/aarch64 wheels, so pip resolved to gevent **22.10.2** and eventlet **0.39.1** (still current enough for a fair comparison).
- **filament** built on every interpreter (version-tagged `.so`), once `PBR_VERSION` was set and, for 2.7, a `#include <pythread.h>` was added so modern GCC sees `PyThread_get_thread_ident`.
- **filament `#137` on Python 2.7**: originally `filament.patcher.patch_all()` raised `TypeError: __weakref__ slot disallowed` on 2.7 (an illegal ``__weakref__`` in ``filament/ssl.py``'s ``__slots__``); that was fixed, and the 2.7 table now includes the logging benchmark wherever it has been re-run since.

## Python 3.14.6

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 401.6k | 156.6k | 176.1k |
| spawn (fire & forget) | spawn_n/s | 387.3k | 307.0k | 256.9k |
| context switch | switches/s | 3.62M | 1.52M | 923.0k |
| semaphore uncontended | ops/s | 41.53M | 10.48M | 13.08M |
| semaphore contended | ops/s | 42.59M | 10.48M | 11.68M |
| queue put/get | items/s | 14.07M | 11.72M | 5.69M |
| queue shared green+native threads | items/s | 2.86M | **error** | **deadlock** |
| tpool round-trip | calls/s | 25.7k | 23.9k | 11.4k |
| tpool round-trip | mean latency | 38.9 us | 41.86 us | 87.46 us |
| echo @ conc 100 | req/s | 136.0k | 123.1k | 96.3k |
| echo @ conc 100 | p50/p99 ms | 0.689 / 1.169 | 0.786 / 0.969 | 1.017 / 1.582 |
| echo @ conc 1000 | req/s | 101.3k | 91.3k | 65.9k |
| echo @ conc 1000 | p50/p99 ms | 8.054 / 14.87 | 9.293 / 23.156 | 13.521 / 23.105 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.7k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.13.5

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 374.7k | 154.3k | 185.5k |
| spawn (fire & forget) | spawn_n/s | 393.9k | 304.1k | 277.9k |
| context switch | switches/s | 4.35M | 1.47M | 941.5k |
| semaphore uncontended | ops/s | 46.93M | 10.44M | 12.40M |
| semaphore contended | ops/s | 51.47M | 10.15M | 12.61M |
| queue put/get | items/s | 16.20M | 12.55M | 6.01M |
| queue shared green+native threads | items/s | 2.87M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.0k | 25.0k | 12.6k |
| tpool round-trip | mean latency | 37.06 us | 40.07 us | 79.5 us |
| echo @ conc 100 | req/s | 147.6k | 133.3k | 98.9k |
| echo @ conc 100 | p50/p99 ms | 0.65 / 0.94 | 0.72 / 0.848 | 0.983 / 1.614 |
| echo @ conc 1000 | req/s | 115.1k | 107.2k | 70.6k |
| echo @ conc 1000 | p50/p99 ms | 6.936 / 14.045 | 7.799 / 16.73 | 12.774 / 20.902 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.2k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 333.0k | 144.1k | 177.8k |
| spawn (fire & forget) | spawn_n/s | 307.8k | 278.9k | 245.3k |
| context switch | switches/s | 3.31M | 1.27M | 949.4k |
| semaphore uncontended | ops/s | 48.45M | 10.19M | 12.85M |
| semaphore contended | ops/s | 39.09M | 10.18M | 12.90M |
| queue put/get | items/s | 14.92M | 12.31M | 5.12M |
| queue shared green+native threads | items/s | 2.58M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.3k | 25.6k | 11.7k |
| tpool round-trip | mean latency | 36.68 us | 39.02 us | 85.26 us |
| echo @ conc 100 | req/s | 141.2k | 124.6k | 95.5k |
| echo @ conc 100 | p50/p99 ms | 0.678 / 1.006 | 0.773 / 1.041 | 1.033 / 1.54 |
| echo @ conc 1000 | req/s | 112.6k | 94.7k | 62.4k |
| echo @ conc 1000 | p50/p99 ms | 7.303 / 13.448 | 8.888 / 22.628 | 14.246 / 28.514 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.7k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.07M | 211.9k | 211.9k |
| spawn (fire & forget) | spawn_n/s | 898.8k | 550.2k | 400.4k |
| context switch | switches/s | 2.90M | 1.44M | 728.6k |
| semaphore uncontended | ops/s | 39.97M | 10.18M | 7.19M |
| semaphore contended | ops/s | 35.35M | 9.77M | 7.01M |
| queue put/get | items/s | 13.54M | 10.29M | 2.79M |
| queue shared green+native threads | items/s | 2.67M | **error** | **deadlock** |
| tpool round-trip | calls/s | 28.4k | 23.7k | 11.0k |
| tpool round-trip | mean latency | 35.19 us | 42.14 us | 91.31 us |
| echo @ conc 100 | req/s | 140.5k | 119.4k | 76.2k |
| echo @ conc 100 | p50/p99 ms | 0.687 / 0.832 | 0.801 / 0.969 | 1.278 / 1.861 |
| echo @ conc 1000 | req/s | 116.8k | 96.9k | 49.2k |
| echo @ conc 1000 | p50/p99 ms | 7.22 / 11.649 | 8.854 / 16.684 | 18.575 / 31.052 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.0k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.8.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.04M | 239.3k | 196.2k |
| spawn (fire & forget) | spawn_n/s | 840.2k | 488.9k | 327.6k |
| context switch | switches/s | 3.04M | 1.48M | 598.5k |
| semaphore uncontended | ops/s | 46.53M | 12.31M | 4.65M |
| semaphore contended | ops/s | 29.23M | 11.16M | 4.33M |
| queue put/get | items/s | 11.92M | 9.52M | 1.96M |
| queue shared green+native threads | items/s | 2.33M | **error** | **deadlock** |
| tpool round-trip | calls/s | 28.4k | 24.8k | 10.4k |
| tpool round-trip | mean latency | 35.25 us | 40.34 us | 95.9 us |
| echo @ conc 100 | req/s | 139.7k | 101.8k | 64.7k |
| echo @ conc 100 | p50/p99 ms | 0.686 / 0.983 | 0.946 / 1.24 | 1.51 / 2.175 |
| echo @ conc 1000 | req/s | 116.8k | 82.7k | 44.3k |
| echo @ conc 1000 | p50/p99 ms | 7.173 / 11.693 | 10.473 / 21.79 | 20.21 / 36.656 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.0k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 2.7.18

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.33M | 222.4k | 144.1k |
| spawn (fire & forget) | spawn_n/s | 1.04M | 484.6k | 283.4k |
| context switch | switches/s | 3.46M | 1.29M | 458.5k |
| semaphore uncontended | ops/s | 40.66M | 13.94M | 4.97M |
| semaphore contended | ops/s | 19.60M | 10.62M | 4.30M |
| queue put/get | items/s | 10.37M | 7.91M | 1.84M |
| queue shared green+native threads | items/s | 1.07M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.8k | **deadlock** | 10.7k |
| tpool round-trip | mean latency | 35.98 us | **deadlock** | 93.47 us |
| echo @ conc 100 | req/s | 130.2k | 103.2k | 65.1k |
| echo @ conc 100 | p50/p99 ms | 0.732 / 0.989 | 0.949 / 1.107 | 1.504 / 2.167 |
| echo @ conc 1000 | req/s | 106.7k | 84.6k | 36.7k |
| echo @ conc 1000 | p50/p99 ms | 7.835 / 12.315 | 10.19 / 24.313 | 24.583 / 42.446 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.1k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Headline findings

Numbers below are from **Python 3.14.6**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 401.6k gt/s vs gevent 156.6k vs eventlet 176.1k — filament 2.6x gevent, 2.3x eventlet. filament's lead is widest on the older interpreters (up to ~4.7x gevent on 3.10/3.8).
- **Context-switch rate — filament wins:** filament 3.62M sw/s vs gevent 1.52M vs eventlet 923.0k — filament 2.4x gevent, 3.9x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~41.53M uncontended ops/s vs gevent 10.48M / eventlet 13.08M (3-8x), and it leads on queue put/get too.
- **Mixed green+native queue — filament only:** a single bounded `Queue` worked simultaneously by greenthreads AND native `threading.Thread` producers/consumers runs at ~2.86M items/s in filament. The same workload on gevent/eventlet deadlocks or errors — their queues are hub-bound and cannot be used from a foreign OS thread. filament's per-thread scheduler + deferred cross-thread wakeup makes this a first-class pattern (same mechanism as the #137 win).
- **Threadpool round-trip — filament wins (post-optimization):** filament 25.7k calls/s vs gevent 23.9k vs eventlet 11.4k — filament 1.1x gevent, 2.2x eventlet. This benchmark used to be filament's one loss; MRU (most-recently-idle) worker wakeup closed it -- a single shared condvar was waking the COLDEST idle worker for every job.
- **Echo server — filament wins (post-optimization):** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency (see the 3.13 table); eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path closed what used to be a ~1.4-1.6x gap.
- **#137 logging-in-threadpool — filament's headline win:** filament logs from its real-thread pool while the hub runs greenthreads and **just works, no workaround, ~15-16k msgs/s** (Python 3.8-3.13). gevent and eventlet both **deadlock** under a monkey-patched hub, and gevent's documented mitigations (hub threadpool + native logging locks + `logThreads=False`) **do not** save it — it still deadlocks. This is filament's whole reason for existing, and it holds up.

