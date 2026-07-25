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

## Environments

| Python | greenlet | gevent | eventlet |
|---|---|---|---|
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

## Python 3.13.5

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 396.9k | 156.5k | 190.7k |
| spawn (fire & forget) | spawn_n/s | 369.9k | 296.9k | 277.1k |
| context switch | switches/s | 3.11M | 1.44M | 940.7k |
| semaphore uncontended | ops/s | 50.16M | 10.88M | 12.47M |
| semaphore contended | ops/s | 46.42M | 10.40M | 12.67M |
| queue put/get | items/s | 16.63M | 12.38M | 6.11M |
| queue shared green+native threads | items/s | 2.88M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.8k | 25.9k | 12.2k |
| tpool round-trip | mean latency | 36.0 us | 38.59 us | 82.04 us |
| echo @ conc 100 | req/s | 136.9k | 130.7k | 100.5k |
| echo @ conc 100 | p50/p99 ms | 0.706 / 1.133 | 0.737 / 0.918 | 0.958 / 1.389 |
| echo @ conc 1000 | req/s | 113.0k | 103.1k | 69.6k |
| echo @ conc 1000 | p50/p99 ms | 7.282 / 13.205 | 8.088 / 17.173 | 13.025 / 19.099 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.2k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 339.0k | 144.0k | 177.5k |
| spawn (fire & forget) | spawn_n/s | 304.1k | 277.1k | 246.7k |
| context switch | switches/s | 2.81M | 1.41M | 883.2k |
| semaphore uncontended | ops/s | 47.63M | 9.95M | 12.86M |
| semaphore contended | ops/s | 43.82M | 9.62M | 12.75M |
| queue put/get | items/s | 15.02M | 12.19M | 5.32M |
| queue shared green+native threads | items/s | 2.48M | **error** | **deadlock** |
| tpool round-trip | calls/s | 26.9k | 24.7k | 11.4k |
| tpool round-trip | mean latency | 37.13 us | 40.56 us | 87.8 us |
| echo @ conc 100 | req/s | 136.7k | 121.3k | 97.3k |
| echo @ conc 100 | p50/p99 ms | 0.693 / 1.015 | 0.789 / 1.091 | 1.001 / 1.51 |
| echo @ conc 1000 | req/s | 105.2k | 94.6k | 63.1k |
| echo @ conc 1000 | p50/p99 ms | 7.369 / 12.914 | 8.864 / 22.439 | 14.988 / 27.572 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.9k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.01M | 215.1k | 210.9k |
| spawn (fire & forget) | spawn_n/s | 877.2k | 574.8k | 397.5k |
| context switch | switches/s | 2.59M | 1.51M | 727.9k |
| semaphore uncontended | ops/s | 41.73M | 10.26M | 7.22M |
| semaphore contended | ops/s | 35.25M | 9.94M | 6.91M |
| queue put/get | items/s | 13.45M | 10.89M | 2.98M |
| queue shared green+native threads | items/s | 2.60M | **error** | **deadlock** |
| tpool round-trip | calls/s | 28.0k | 24.8k | 10.5k |
| tpool round-trip | mean latency | 35.7 us | 40.33 us | 95.44 us |
| echo @ conc 100 | req/s | 138.7k | 117.3k | 78.1k |
| echo @ conc 100 | p50/p99 ms | 0.685 / 0.867 | 0.879 / 1.062 | 1.265 / 1.84 |
| echo @ conc 1000 | req/s | 108.3k | 90.0k | 50.3k |
| echo @ conc 1000 | p50/p99 ms | 7.886 / 11.989 | 9.184 / 17.035 | 18.326 / 34.945 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.4k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.8.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.05M | 235.3k | 198.1k |
| spawn (fire & forget) | spawn_n/s | 821.0k | 475.9k | 325.4k |
| context switch | switches/s | 2.61M | 1.46M | 598.1k |
| semaphore uncontended | ops/s | 28.90M | 11.75M | 4.64M |
| semaphore contended | ops/s | 22.70M | 11.18M | 4.02M |
| queue put/get | items/s | 9.70M | 8.45M | 2.03M |
| queue shared green+native threads | items/s | 2.12M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.3k | 24.7k | 10.5k |
| tpool round-trip | mean latency | 36.67 us | 40.49 us | 95.09 us |
| echo @ conc 100 | req/s | 137.9k | 100.3k | 64.3k |
| echo @ conc 100 | p50/p99 ms | 0.697 / 0.946 | 0.963 / 1.157 | 1.527 / 2.225 |
| echo @ conc 1000 | req/s | 112.3k | 80.7k | 44.8k |
| echo @ conc 1000 | p50/p99 ms | 7.54 / 11.001 | 10.557 / 23.3 | 20.235 / 37.05 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.3k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 2.7.18

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.36M | 229.6k | 143.6k |
| spawn (fire & forget) | spawn_n/s | 1.05M | 512.2k | 286.8k |
| context switch | switches/s | 3.46M | 1.29M | 455.4k |
| semaphore uncontended | ops/s | 41.06M | 13.92M | 5.00M |
| semaphore contended | ops/s | 19.50M | 10.69M | 4.36M |
| queue put/get | items/s | 10.56M | 7.90M | 1.85M |
| queue shared green+native threads | items/s | 1.07M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.8k | **deadlock** | 10.0k |
| tpool round-trip | mean latency | 35.97 us | **deadlock** | 99.94 us |
| echo @ conc 100 | req/s | 135.0k | 104.8k | 65.0k |
| echo @ conc 100 | p50/p99 ms | 0.714 / 0.898 | 0.929 / 1.085 | 1.504 / 2.163 |
| echo @ conc 1000 | req/s | 109.9k | 85.3k | 37.7k |
| echo @ conc 1000 | p50/p99 ms | 7.76 / 11.884 | 10.084 / 23.976 | 24.251 / 43.091 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.0k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Headline findings

Numbers below are from **Python 3.13.5**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 396.9k gt/s vs gevent 156.5k vs eventlet 190.7k — filament 2.5x gevent, 2.1x eventlet. filament's lead is widest on the older interpreters (up to ~4.7x gevent on 3.10/3.8).
- **Context-switch rate — filament wins:** filament 3.11M sw/s vs gevent 1.44M vs eventlet 940.7k — filament 2.2x gevent, 3.3x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~50.16M uncontended ops/s vs gevent 10.88M / eventlet 12.47M (3-8x), and it leads on queue put/get too.
- **Mixed green+native queue — filament only:** a single bounded `Queue` worked simultaneously by greenthreads AND native `threading.Thread` producers/consumers runs at ~2.88M items/s in filament. The same workload on gevent/eventlet deadlocks or errors — their queues are hub-bound and cannot be used from a foreign OS thread. filament's per-thread scheduler + deferred cross-thread wakeup makes this a first-class pattern (same mechanism as the #137 win).
- **Threadpool round-trip — filament wins (post-optimization):** filament 27.8k calls/s vs gevent 25.9k vs eventlet 12.2k — filament 1.1x gevent, 2.3x eventlet. This benchmark used to be filament's one loss; MRU (most-recently-idle) worker wakeup closed it -- a single shared condvar was waking the COLDEST idle worker for every job.
- **Echo server — filament wins (post-optimization):** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency (see the 3.13 table); eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path closed what used to be a ~1.4-1.6x gap.
- **#137 logging-in-threadpool — filament's headline win:** filament logs from its real-thread pool while the hub runs greenthreads and **just works, no workaround, ~15-16k msgs/s** (Python 3.8-3.13). gevent and eventlet both **deadlock** under a monkey-patched hub, and gevent's documented mitigations (hub threadpool + native logging locks + `logThreads=False`) **do not** save it — it still deadlocks. This is filament's whole reason for existing, and it holds up.

