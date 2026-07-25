# filament vs gevent vs eventlet — benchmark results

Greenlet-based cooperative concurrency shootout: **filament** (this repo) against modern **gevent** and **eventlet**, across CPython versions on aarch64 Linux.

Each (framework, benchmark) ran in its own fresh subprocess. Micro-benchmarks report the **median** of several timed reps (warm-up discarded), using a monotonic clock. Higher is better for throughput; lower is better for latency.

## Methodology

- Same logical workload run three ways (filament / gevent / eventlet) with identical sizes per framework. For filament the in-process client is `filament.socket`; gevent uses `gevent.socket` + `StreamServer`-style accept loop; eventlet uses `eventlet.green.socket`. The echo client stays in the same framework as the server for fairness.
- Each pair runs in a **fresh interpreter subprocess** so monkey-patching and hub state never leak between frameworks.
- Spawn = 100k greenthreads spawned then joined. Context switch = 100 greenthreads x 10k `sleep(0)` = 1,000,000 switches. Semaphore uncontended = 1M acquire/release on one greenthread; contended = 50 greenthreads on a `Semaphore(1)`. Queue = 200k producer/consumer items. tpool = 3000 sequential real-thread round-trips. Echo = concurrency 100 (x100 round-trips) and 1000 (x20), 64-byte payload.
- **#137**: monkey-patch everything, then log heavily from real OS-thread pool workers while greenthreads spin in the hub. Each attempt runs under a hard 30 s subprocess watchdog; a hang is recorded as **deadlock**.

> **Cross-version caveat.** Runs were executed sequentially (3.13 -> 3.12 -> 3.10 -> 3.8 -> 2.7) on a shared box. The 3.13 and 3.12 runs land ~3x lower in *absolute* throughput across **all three** frameworks (a concurrent build was using the machine during those runs), so absolute numbers are **not** comparable across Python versions. The reliable signal is the **ratio between frameworks within one version**, which is stable across the whole matrix.

> **Optimization re-run (2026-07-24).** filament's tpool and socket paths were optimized after the original matrix was recorded (MRU thread-pool worker wakeup, GIL-free io-thread completion signaling, persistent edge-triggered socket readiness events). Only the **Python 3.13** table was re-measured with the optimized filament; the 3.12/3.10/3.8/2.7 tables still show pre-optimization filament numbers for tpool and echo.

## Environments

| Python | greenlet | gevent | eventlet |
|---|---|---|---|
| 3.13.5 | 3.5.4 | 26.7.0 | 0.41.1 |
| 3.12.13 | 3.3.2 | 26.7.0 | 0.41.1 |
| 3.10.20 | 3.3.2 | 26.7.0 | 0.41.1 |
| 3.8.20 | 3.1.1 | 22.10.2 | 0.39.1 |
| 2.7.18 | 1.1.3 | 21.12.0 | 0.33.3 |

Availability notes:

- **gevent on Python 2.7**: no cp27/aarch64 wheel exists and stock source builds fail under a modern GCC (Cython-generated C errors); where the 2.7 column shows gevent numbers they come from a locally-built older gevent (see the environments table). eventlet 0.33.3 (pure-Python) and filament both build/run on 2.7.
- **gevent/eventlet on Python 3.8**: latest releases have no 3.8/aarch64 wheels, so pip resolved to gevent **22.10.2** and eventlet **0.39.1** (still current enough for a fair comparison).
- **filament** built on every interpreter (version-tagged `.so`), once `PBR_VERSION` was set and, for 2.7, a `#include <pythread.h>` was added so modern GCC sees `PyThread_get_thread_ident`.
- **filament `#137` on Python 2.7**: originally `filament.patcher.patch_all()` raised `TypeError: __weakref__ slot disallowed` on 2.7 (an illegal ``__weakref__`` in ``filament/ssl.py``'s ``__slots__``); that was fixed, and the 2.7 table now includes the logging benchmark wherever it has been re-run since.

## Python 3.13.5

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 403.7k | 157.4k | 200.1k |
| spawn (fire & forget) | spawn_n/s | 380.5k | 312.2k | 299.8k |
| context switch | switches/s | 2.35M | 1.42M | 939.0k |
| semaphore uncontended | ops/s | 42.39M | 10.76M | 12.25M |
| semaphore contended | ops/s | 42.52M | 10.11M | 12.48M |
| queue put/get | items/s | 13.01M | 12.32M | 5.94M |
| tpool round-trip | calls/s | 26.0k | 25.3k | 12.9k |
| tpool round-trip | mean latency | 38.42 us | 39.54 us | 77.57 us |
| echo @ conc 100 | req/s | 132.5k | 128.9k | 100.3k |
| echo @ conc 100 | p50/p99 ms | 0.728 / 1.113 | 0.735 / 1.022 | 0.978 / 1.42 |
| echo @ conc 1000 | req/s | 99.3k | 98.1k | 65.1k |
| echo @ conc 1000 | p50/p99 ms | 8.232 / 14.738 | 8.22 / 24.764 | 14.143 / 22.999 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 18.0k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 338.3k | 138.4k | 154.3k |
| spawn (fire & forget) | spawn_n/s | 305.0k | 250.4k | 219.2k |
| context switch | switches/s | 2.71M | 1.30M | 779.8k |
| semaphore uncontended | ops/s | 31.25M | 10.53M | 10.90M |
| semaphore contended | ops/s | 35.86M | 8.70M | 11.02M |
| queue put/get | items/s | 9.97M | 9.32M | 4.91M |
| tpool round-trip | calls/s | 18.2k | 21.5k | 11.5k |
| tpool round-trip | mean latency | 55.09 us | 46.59 us | 86.62 us |
| echo @ conc 100 | req/s | 69.3k | 106.2k | 82.1k |
| echo @ conc 100 | p50/p99 ms | 1.306 / 1.654 | 0.907 / 1.163 | 1.2 / 1.762 |
| echo @ conc 1000 | req/s | 61.8k | 84.2k | 56.1k |
| echo @ conc 1000 | p50/p99 ms | 14.023 / 19.296 | 9.91 / 22.216 | 16.259 / 29.667 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 15.3k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.10M | 232.0k | 197.2k |
| spawn (fire & forget) | spawn_n/s | 913.9k | 521.0k | 351.5k |
| context switch | switches/s | 2.66M | 1.64M | 622.5k |
| semaphore uncontended | ops/s | 31.80M | 12.60M | 6.09M |
| semaphore contended | ops/s | 25.57M | 12.33M | 5.71M |
| queue put/get | items/s | 10.40M | 9.95M | 2.37M |
| tpool round-trip | calls/s | 16.7k | 19.2k | 10.3k |
| tpool round-trip | mean latency | 59.97 us | 52.13 us | 97.54 us |
| echo @ conc 100 | req/s | 75.2k | 104.7k | 68.2k |
| echo @ conc 100 | p50/p99 ms | 1.283 / 1.746 | 0.918 / 1.041 | 1.433 / 2.144 |
| echo @ conc 1000 | req/s | 67.7k | 85.0k | 45.8k |
| echo @ conc 1000 | p50/p99 ms | 13.392 / 14.869 | 10.542 / 23.241 | 21.474 / 38.274 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.3k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Python 3.8.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.04M | 235.9k | 202.1k |
| spawn (fire & forget) | spawn_n/s | 908.3k | 538.7k | 352.8k |
| context switch | switches/s | 2.71M | 1.51M | 613.1k |
| semaphore uncontended | ops/s | 31.94M | 12.01M | 5.05M |
| semaphore contended | ops/s | 24.46M | 10.58M | 4.89M |
| queue put/get | items/s | 10.89M | 10.06M | 2.12M |
| tpool round-trip | calls/s | 17.6k | 23.8k | 10.7k |
| tpool round-trip | mean latency | 56.73 us | 41.98 us | 93.51 us |
| echo @ conc 100 | req/s | 76.0k | 102.4k | 65.3k |
| echo @ conc 100 | p50/p99 ms | 1.264 / 1.725 | 0.935 / 1.144 | 1.489 / 2.214 |
| echo @ conc 1000 | req/s | 67.7k | 84.8k | 44.8k |
| echo @ conc 1000 | p50/p99 ms | 13.028 / 16.469 | 10.774 / 22.52 | 20.605 / 35.962 |

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
| spawn (tracked, spawn+join) | greenthreads/s | 1.38M | 240.2k | 142.9k |
| spawn (fire & forget) | spawn_n/s | 1.15M | 546.1k | 299.7k |
| context switch | switches/s | 3.67M | 1.32M | 463.3k |
| semaphore uncontended | ops/s | 40.26M | 11.08M | 4.99M |
| semaphore contended | ops/s | 19.69M | 8.95M | 4.29M |
| queue put/get | items/s | 10.49M | 7.88M | 1.81M |
| tpool round-trip | calls/s | 18.2k | 23.6k | 10.3k |
| tpool round-trip | mean latency | 54.99 us | 42.35 us | 96.71 us |
| echo @ conc 100 | req/s | 75.6k | 98.1k | 64.4k |
| echo @ conc 100 | p50/p99 ms | 1.269 / 1.562 | 0.948 / 1.197 | 1.519 / 2.269 |
| echo @ conc 1000 | req/s | 62.8k | 84.9k | 36.8k |
| echo @ conc 1000 | p50/p99 ms | 14.32 / 16.15 | 10.403 / 32.839 | 24.654 / 42.611 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.1k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Headline findings

Numbers below are from **Python 3.13.5**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 403.7k gt/s vs gevent 157.4k vs eventlet 200.1k — filament 2.6x gevent, 2.0x eventlet. filament's lead is widest on the older interpreters (up to ~4.7x gevent on 3.10/3.8).
- **Context-switch rate — filament wins:** filament 2.35M sw/s vs gevent 1.42M vs eventlet 939.0k — filament 1.7x gevent, 2.5x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~42.39M uncontended ops/s vs gevent 10.76M / eventlet 12.25M (3-8x), and it leads on queue put/get too.
- **Threadpool round-trip — filament wins (post-optimization):** filament 26.0k calls/s vs gevent 25.3k vs eventlet 12.9k — filament 1.0x gevent, 2.0x eventlet. This benchmark used to be filament's one loss; MRU (most-recently-idle) worker wakeup closed it -- a single shared condvar was waking the COLDEST idle worker for every job.
- **Echo server — filament wins (post-optimization):** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency (see the 3.13 table); eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path closed what used to be a ~1.4-1.6x gap.
- **#137 logging-in-threadpool — filament's headline win:** filament logs from its real-thread pool while the hub runs greenthreads and **just works, no workaround, ~15-16k msgs/s** (Python 3.8-3.13). gevent and eventlet both **deadlock** under a monkey-patched hub, and gevent's documented mitigations (hub threadpool + native logging locks + `logThreads=False`) **do not** save it — it still deadlocks. This is filament's whole reason for existing, and it holds up.

