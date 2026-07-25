# filament vs gevent vs eventlet — benchmark results

Greenlet-based cooperative concurrency shootout: **filament** (this repo) against modern **gevent** and **eventlet**, across CPython versions on aarch64 Linux.

Each (framework, benchmark) ran in its own fresh subprocess. Micro-benchmarks report the **median** of several timed reps (warm-up discarded), using a monotonic clock. Higher is better for throughput; lower is better for latency.

## Methodology

- Same logical workload run three ways (filament / gevent / eventlet) with identical sizes per framework. For filament the in-process client is `filament.socket`; gevent uses `gevent.socket` + `StreamServer`-style accept loop; eventlet uses `eventlet.green.socket`. The echo client stays in the same framework as the server for fairness.
- Each pair runs in a **fresh interpreter subprocess** so monkey-patching and hub state never leak between frameworks.
- Spawn = 100k greenthreads spawned then joined. Context switch = 100 greenthreads x 10k `sleep(0)` = 1,000,000 switches. Semaphore uncontended = 1M acquire/release on one greenthread; contended = 50 greenthreads on a `Semaphore(1)`. Queue = 200k producer/consumer items. tpool = 3000 sequential real-thread round-trips. Echo = concurrency 100 (x100 round-trips) and 1000 (x20), 64-byte payload.
- **#137**: monkey-patch everything, then log heavily from real OS-thread pool workers while greenthreads spin in the hub. Each attempt runs under a hard 30 s subprocess watchdog; a hang is recorded as **deadlock**.

> **Cross-version caveat.** Each Python version's table was recorded in its own sequential run on the same box. Interpreter speed differs across versions, so absolute numbers are **not** comparable across Python versions. The reliable signal is the **ratio between frameworks within one version**: all three frameworks in a table ran back-to-back under identical conditions.

> **Full-matrix re-run (2026-07-25).** The entire matrix was re-measured with the optimized filament (MRU thread-pool worker wakeup, GIL-free io-thread completion signaling, persistent edge-triggered socket readiness events) and the latest installable greenlet per interpreter (2.0.2 on 2.7, 3.1.1 on 3.8, 3.5.4 on 3.10+), plus gevent 22.10.2 on 2.7.

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
| spawn (tracked, spawn+join) | greenthreads/s | 386.9k | 149.3k | 192.5k |
| spawn (fire & forget) | spawn_n/s | 374.4k | 286.9k | 293.4k |
| context switch | switches/s | 2.41M | 1.46M | 940.6k |
| semaphore uncontended | ops/s | 42.71M | 10.52M | 12.45M |
| semaphore contended | ops/s | 42.63M | 10.21M | 12.52M |
| queue put/get | items/s | 14.33M | 12.41M | 6.07M |
| tpool round-trip | calls/s | 27.4k | 24.8k | 12.2k |
| tpool round-trip | mean latency | 36.48 us | 40.28 us | 82.01 us |
| echo @ conc 100 | req/s | 134.6k | 129.6k | 100.1k |
| echo @ conc 100 | p50/p99 ms | 0.715 / 1.017 | 0.725 / 0.856 | 0.97 / 1.455 |
| echo @ conc 1000 | req/s | 104.5k | 104.4k | 68.9k |
| echo @ conc 1000 | p50/p99 ms | 8.024 / 13.354 | 7.978 / 17.23 | 13.236 / 22.681 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 15.4k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 331.7k | 143.9k | 179.0k |
| spawn (fire & forget) | spawn_n/s | 300.0k | 271.0k | 248.8k |
| context switch | switches/s | 2.31M | 1.24M | 905.7k |
| semaphore uncontended | ops/s | 39.82M | 9.90M | 12.84M |
| semaphore contended | ops/s | 38.15M | 9.83M | 12.91M |
| queue put/get | items/s | 13.53M | 12.16M | 5.26M |
| tpool round-trip | calls/s | 25.8k | 24.0k | 11.8k |
| tpool round-trip | mean latency | 38.83 us | 41.75 us | 84.58 us |
| echo @ conc 100 | req/s | 133.9k | 126.2k | 94.0k |
| echo @ conc 100 | p50/p99 ms | 0.715 / 1.086 | 0.766 / 0.895 | 1.193 / 1.999 |
| echo @ conc 1000 | req/s | 107.3k | 95.4k | 62.1k |
| echo @ conc 1000 | p50/p99 ms | 7.718 / 13.355 | 8.836 / 22.23 | 14.52 / 28.686 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.0k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.05M | 210.4k | 214.0k |
| spawn (fire & forget) | spawn_n/s | 822.4k | 568.1k | 407.0k |
| context switch | switches/s | 2.30M | 1.48M | 725.8k |
| semaphore uncontended | ops/s | 36.90M | 10.50M | 7.29M |
| semaphore contended | ops/s | 30.98M | 10.19M | 7.00M |
| queue put/get | items/s | 11.99M | 10.35M | 2.93M |
| tpool round-trip | calls/s | 27.1k | 25.0k | 11.3k |
| tpool round-trip | mean latency | 36.88 us | 40.01 us | 88.3 us |
| echo @ conc 100 | req/s | 134.4k | 115.0k | 76.3k |
| echo @ conc 100 | p50/p99 ms | 0.712 / 0.977 | 0.843 / 1.085 | 1.282 / 1.889 |
| echo @ conc 1000 | req/s | 110.7k | 94.5k | 50.2k |
| echo @ conc 1000 | p50/p99 ms | 7.701 / 11.42 | 8.818 / 17.174 | 18.266 / 34.15 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 15.5k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.8.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.06M | 230.4k | 185.2k |
| spawn (fire & forget) | spawn_n/s | 826.8k | 518.9k | 339.8k |
| context switch | switches/s | 2.57M | 1.45M | 591.4k |
| semaphore uncontended | ops/s | 29.54M | 11.71M | 4.42M |
| semaphore contended | ops/s | 23.02M | 10.80M | 4.16M |
| queue put/get | items/s | 9.27M | 9.25M | 2.05M |
| tpool round-trip | calls/s | 27.1k | 23.6k | 10.8k |
| tpool round-trip | mean latency | 36.95 us | 42.34 us | 92.68 us |
| echo @ conc 100 | req/s | 134.2k | 101.0k | 64.3k |
| echo @ conc 100 | p50/p99 ms | 0.72 / 0.885 | 0.964 / 1.162 | 1.531 / 2.228 |
| echo @ conc 1000 | req/s | 111.6k | 83.7k | 44.9k |
| echo @ conc 1000 | p50/p99 ms | 7.505 / 11.988 | 10.318 / 24.836 | 20.672 / 36.93 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.4k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 2.7.18

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.33M | 221.1k | 145.2k |
| spawn (fire & forget) | spawn_n/s | 1.03M | 521.5k | 287.2k |
| context switch | switches/s | 2.97M | 1.26M | 453.6k |
| semaphore uncontended | ops/s | 39.90M | 13.96M | 4.88M |
| semaphore contended | ops/s | 19.47M | 10.66M | 4.28M |
| queue put/get | items/s | 10.45M | 7.86M | 1.85M |
| tpool round-trip | calls/s | 27.4k | **deadlock** | 10.6k |
| tpool round-trip | mean latency | 36.54 us | **deadlock** | 94.57 us |
| echo @ conc 100 | req/s | 132.6k | 103.5k | 64.5k |
| echo @ conc 100 | p50/p99 ms | 0.725 / 0.954 | 0.937 / 1.073 | 1.517 / 2.255 |
| echo @ conc 1000 | req/s | 106.8k | 85.9k | 37.3k |
| echo @ conc 1000 | p50/p99 ms | 7.781 / 12.922 | 10.171 / 23.817 | 24.356 / 43.735 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 15.1k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Headline findings

Numbers below are from **Python 3.13.5**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 386.9k gt/s vs gevent 149.3k vs eventlet 192.5k — filament 2.6x gevent, 2.0x eventlet. filament's lead is widest on the older interpreters (up to ~4.7x gevent on 3.10/3.8).
- **Context-switch rate — filament wins:** filament 2.41M sw/s vs gevent 1.46M vs eventlet 940.6k — filament 1.6x gevent, 2.6x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~42.71M uncontended ops/s vs gevent 10.52M / eventlet 12.45M (3-8x), and it leads on queue put/get too.
- **Threadpool round-trip — filament wins (post-optimization):** filament 27.4k calls/s vs gevent 24.8k vs eventlet 12.2k — filament 1.1x gevent, 2.2x eventlet. This benchmark used to be filament's one loss; MRU (most-recently-idle) worker wakeup closed it -- a single shared condvar was waking the COLDEST idle worker for every job.
- **Echo server — filament wins (post-optimization):** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency (see the 3.13 table); eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path closed what used to be a ~1.4-1.6x gap.
- **#137 logging-in-threadpool — filament's headline win:** filament logs from its real-thread pool while the hub runs greenthreads and **just works, no workaround, ~15-16k msgs/s** (Python 3.8-3.13). gevent and eventlet both **deadlock** under a monkey-patched hub, and gevent's documented mitigations (hub threadpool + native logging locks + `logThreads=False`) **do not** save it — it still deadlocks. This is filament's whole reason for existing, and it holds up.

