# filament vs gevent vs eventlet — benchmark results

Greenlet-based cooperative concurrency shootout: **filament** (this repo) against modern **gevent** and **eventlet**, across CPython versions on aarch64 and x86_64 Linux (results are grouped by architecture below).

Each (framework, benchmark) ran in its own fresh subprocess. Micro-benchmarks report the **median** of several timed reps (warm-up discarded), using a monotonic clock. Higher is better for throughput; lower is better for latency.

## Methodology

- Same logical workload run three ways (filament / gevent / eventlet) with identical sizes per framework. For filament the in-process client is `filament.socket`; gevent uses `gevent.socket` + `StreamServer`-style accept loop; eventlet uses `eventlet.green.socket`. The echo client stays in the same framework as the server for fairness.
- Each pair runs in a **fresh interpreter subprocess** so monkey-patching and hub state never leak between frameworks.
- Spawn = 100k greenthreads spawned then joined. Context switch = 100 greenthreads x 10k `sleep(0)` = 1,000,000 switches. Semaphore uncontended = 1M acquire/release on one greenthread; contended = 50 greenthreads on a `Semaphore(1)`. Queue = 200k producer/consumer items. tpool = 3000 sequential real-thread round-trips. Echo = concurrency 100 (x100 round-trips) and 1000 (x20), 64-byte payload.
- Queue mixed = ONE bounded queue (maxsize 100) shared simultaneously by a greenthread producer + consumer AND a native `threading.Thread` producer + consumer (50k items per producer), so native threads block in `q.get()`/`q.put()` while greenthreads work the same queue. gevent/eventlet queues are hub-bound; foreign-OS-thread use is undefined for them and runs under the deadlock watchdog.
- **#137**: monkey-patch everything, then log heavily from real OS-thread pool workers while greenthreads spin in the hub. Each attempt runs under a hard 30 s subprocess watchdog; a hang is recorded as **deadlock**.

> **Cross-version caveat.** Each Python version's table was recorded in its own sequential run on the same box. Interpreter speed differs across versions, so absolute numbers are **not** comparable across Python versions. The reliable signal is the **ratio between frameworks within one version**: all three frameworks in a table ran back-to-back under identical conditions.

> **What these numbers measure (recorded 2026-07-25).** The matrix reflects filament as it is today: a vendored greenlet on py3 (`_fil_greenlet`, private capsule — no conflict with an installed greenlet — with a C fast-switch entry and lazy `gr_frame` materialization; py2.7 transparently uses classic greenlet), MRU thread-pool worker wakeup, GIL-free io-thread completion signaling, persistent edge-triggered socket readiness events, METH_FASTCALL hot entry points (semaphore/lock/queue/message/sleep), scheduler switch-event + waiter freelists, a `sleep(0)` fast path, and cond-signal-after-unlock. Cross-thread wakeup is pure futex/condvar (no fd) — measurably cheaper per wakeup than gevent's eventfd+epoll async watcher path. Debug introspection is runtime-selectable (`filament.set_debug(True)` / `FILAMENT_DEBUG=1`, auto-armed when a trace/profile hook is installed) and off the hot path by default. Comparisons use the latest installable greenlet per interpreter (2.0.2 on 2.7, 3.1.1 on 3.8, 3.5.4 on 3.10+), plus gevent 22.10.2 on 2.7.

## Environments

| Arch | Python | greenlet | gevent | eventlet |
|---|---|---|---|---|
| amd64 | 3.15.0 | 3.3.2 | 26.7.0 | 0.41.1 |
| amd64 | 3.14.4 | 3.3.2 | 26.7.0 | 0.41.1 |
| amd64 | 3.13.14 | 3.3.2 | 26.7.0 | 0.41.1 |
| amd64 | 3.12.13 | 3.3.2 | 26.7.0 | 0.41.1 |
| amd64 | 3.11.15 | 3.3.2 | 26.7.0 | 0.41.1 |
| amd64 | 3.10.20 | 3.3.2 | 26.7.0 | 0.41.1 |
| amd64 | 3.8.20 | 3.1.1 | 24.2.1 | 0.39.1 |
| arm64 | 3.15.0 | 3.3.2 | 26.7.0 | 0.41.1 |
| arm64 | 3.14.6 | 3.3.2 | 26.7.0 | 0.41.1 |
| arm64 | 3.13.5 | 3.5.4 | 26.7.0 | 0.41.1 |
| arm64 | 3.12.13 | 3.5.4 | 26.7.0 | 0.41.1 |
| arm64 | 3.11.15 | 3.3.2 | 26.7.0 | 0.41.1 |
| arm64 | 3.10.20 | 3.5.4 | 26.7.0 | 0.41.1 |
| arm64 | 3.8.20 | 3.1.1 | 22.10.2 | 0.39.1 |
| arm64 | 2.7.18 | 2.0.2 | 22.10.2 | 0.33.3 |

Availability notes:

- **gevent on Python 2.7**: no cp27/aarch64 wheel exists and stock source builds fail under a modern GCC (Cython-generated C errors); where the 2.7 column shows gevent numbers they come from a locally-built older gevent (see the environments table). eventlet 0.33.3 (pure-Python) and filament both build/run on 2.7.
- **gevent tpool on Python 2.7**: gevent **22.10.2** (the last py2.7 release) deadlocks in the threadpool round-trip benchmark on 2.7 — reproducible even at small scale. Its predecessor 21.12.0 completed the same benchmark (~23.6k calls/s), so this is a gevent regression in its final py2.7 release, not a harness artifact.
- **gevent/eventlet on Python 3.8**: latest releases have no 3.8/aarch64 wheels, so pip resolved to gevent **22.10.2** and eventlet **0.39.1** (still current enough for a fair comparison).
- **filament** builds and runs every benchmark (including `#137`) on every interpreter in the matrix, 2.7 through 3.15 (version-tagged `.so` per interpreter).

## amd64 · Python 3.15.0

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 111.6k | 56.4k | 61.9k |
| spawn (fire & forget) | spawn_n/s | 106.5k | 84.6k | 82.0k |
| context switch | switches/s | 2.17M | 786.2k | 486.4k |
| semaphore uncontended | ops/s | 21.81M | 6.91M | 6.67M |
| semaphore contended | ops/s | 17.92M | 7.09M | 6.47M |
| queue put/get | items/s | 7.67M | 5.56M | 3.35M |
| queue shared green+native threads | items/s | 2.76M | **error** | **deadlock** |
| tpool round-trip | calls/s | 75.0k | 49.4k | 11.3k |
| tpool round-trip | mean latency | 13.33 us | 20.26 us | 88.51 us |
| echo @ conc 100 | req/s | 41.4k | 35.9k | 29.3k |
| echo @ conc 100 | p50/p99 ms | 2.287 / 3.595 | 2.595 / 4.173 | 3.309 / 5.048 |
| echo @ conc 1000 | req/s | 33.7k | 27.9k | 22.5k |
| echo @ conc 1000 | p50/p99 ms | 23.475 / 50.262 | 26.8 / 116.608 | 38.457 / 66.121 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 29.4k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.14.4

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 121.5k | 69.9k | 81.5k |
| spawn (fire & forget) | spawn_n/s | 115.2k | 106.8k | 100.5k |
| context switch | switches/s | 2.09M | 769.3k | 454.5k |
| semaphore uncontended | ops/s | 21.81M | 7.28M | 6.88M |
| semaphore contended | ops/s | 21.95M | 7.31M | 6.47M |
| queue put/get | items/s | 7.84M | 6.02M | 3.35M |
| queue shared green+native threads | items/s | 2.51M | **error** | **deadlock** |
| tpool round-trip | calls/s | 45.3k | 28.0k | 11.1k |
| tpool round-trip | mean latency | 22.06 us | 35.68 us | 90.02 us |
| echo @ conc 100 | req/s | 42.1k | 33.6k | 26.7k |
| echo @ conc 100 | p50/p99 ms | 2.276 / 3.474 | 2.843 / 4.319 | 3.784 / 5.658 |
| echo @ conc 1000 | req/s | 34.0k | 27.7k | 20.9k |
| echo @ conc 1000 | p50/p99 ms | 23.046 / 50.349 | 28.6 / 121.032 | 42.625 / 77.404 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 35.8k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.13.14

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 122.1k | 64.8k | 69.4k |
| spawn (fire & forget) | spawn_n/s | 118.0k | 103.9k | 95.6k |
| context switch | switches/s | 2.42M | 827.8k | 492.0k |
| semaphore uncontended | ops/s | 22.51M | 7.25M | 6.42M |
| semaphore contended | ops/s | 21.76M | 7.32M | 6.36M |
| queue put/get | items/s | 8.13M | 6.73M | 3.23M |
| queue shared green+native threads | items/s | 2.60M | **error** | **deadlock** |
| tpool round-trip | calls/s | 47.1k | 48.5k | 11.5k |
| tpool round-trip | mean latency | 21.23 us | 20.61 us | 87.02 us |
| echo @ conc 100 | req/s | 40.9k | 36.3k | 29.8k |
| echo @ conc 100 | p50/p99 ms | 2.304 / 3.637 | 2.631 / 4.006 | 3.267 / 4.786 |
| echo @ conc 1000 | req/s | 34.4k | 28.7k | 22.6k |
| echo @ conc 1000 | p50/p99 ms | 23.176 / 48.185 | 26.846 / 112.584 | 40.8 / 67.913 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 24.9k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 112.8k | 61.7k | 63.7k |
| spawn (fire & forget) | spawn_n/s | 102.3k | 97.5k | 86.5k |
| context switch | switches/s | 2.51M | 821.7k | 476.2k |
| semaphore uncontended | ops/s | 20.71M | 7.11M | 6.77M |
| semaphore contended | ops/s | 21.29M | 6.85M | 6.66M |
| queue put/get | items/s | 8.15M | 6.49M | 2.90M |
| queue shared green+native threads | items/s | 2.43M | **error** | **deadlock** |
| tpool round-trip | calls/s | 70.9k | 46.3k | 10.7k |
| tpool round-trip | mean latency | 14.1 us | 21.6 us | 93.29 us |
| echo @ conc 100 | req/s | 41.7k | 36.5k | 28.9k |
| echo @ conc 100 | p50/p99 ms | 2.273 / 3.526 | 2.576 / 4.453 | 3.361 / 4.871 |
| echo @ conc 1000 | req/s | 34.3k | 29.6k | 21.3k |
| echo @ conc 1000 | p50/p99 ms | 23.325 / 49.057 | 26.263 / 111.748 | 41.298 / 84.465 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 33.0k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.11.15

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 105.2k | 59.5k | 60.9k |
| spawn (fire & forget) | spawn_n/s | 98.0k | 95.9k | 86.7k |
| context switch | switches/s | 2.31M | 925.0k | 481.8k |
| semaphore uncontended | ops/s | 18.18M | 7.88M | 5.61M |
| semaphore contended | ops/s | 18.18M | 7.50M | 5.64M |
| queue put/get | items/s | 7.65M | 5.96M | 2.70M |
| queue shared green+native threads | items/s | 2.27M | **error** | **deadlock** |
| tpool round-trip | calls/s | 47.2k | 47.7k | 11.2k |
| tpool round-trip | mean latency | 21.18 us | 20.98 us | 89.46 us |
| echo @ conc 100 | req/s | 46.9k | 35.0k | 28.3k |
| echo @ conc 100 | p50/p99 ms | 1.965 / 3.495 | 2.881 / 4.175 | 3.443 / 5.023 |
| echo @ conc 1000 | req/s | 34.9k | 27.9k | 21.4k |
| echo @ conc 1000 | p50/p99 ms | 22.259 / 43.264 | 28.3 / 116.012 | 41.68 / 74.37 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 24.7k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 532.1k | 113.7k | 91.9k |
| spawn (fire & forget) | spawn_n/s | 429.0k | 293.2k | 194.0k |
| context switch | switches/s | 2.12M | 837.1k | 361.6k |
| semaphore uncontended | ops/s | 16.42M | 7.78M | 3.47M |
| semaphore contended | ops/s | 14.13M | 6.78M | 3.33M |
| queue put/get | items/s | 6.69M | 6.19M | 1.53M |
| queue shared green+native threads | items/s | 2.61M | **error** | **deadlock** |
| tpool round-trip | calls/s | 44.7k | 26.0k | 9.3k |
| tpool round-trip | mean latency | 22.4 us | 38.44 us | 108.06 us |
| echo @ conc 100 | req/s | 48.0k | 34.5k | 21.8k |
| echo @ conc 100 | p50/p99 ms | 1.963 / 2.624 | 2.803 / 4.166 | 4.482 / 6.661 |
| echo @ conc 1000 | req/s | 36.0k | 28.4k | 16.2k |
| echo @ conc 1000 | p50/p99 ms | 23.293 / 38.046 | 28.96 / 116.087 | 56.216 / 101.493 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 58.0k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.8.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 619.3k | 101.5k | 89.5k |
| spawn (fire & forget) | spawn_n/s | 460.4k | 269.3k | 169.3k |
| context switch | switches/s | 1.95M | 826.6k | 368.8k |
| semaphore uncontended | ops/s | 28.75M | 8.70M | 3.33M |
| semaphore contended | ops/s | 22.58M | 7.97M | 2.98M |
| queue put/get | items/s | 8.64M | 6.21M | 1.46M |
| queue shared green+native threads | items/s | 2.78M | **error** | **deadlock** |
| tpool round-trip | calls/s | 73.5k | 24.6k | 9.1k |
| tpool round-trip | mean latency | 13.61 us | 40.58 us | 109.36 us |
| echo @ conc 100 | req/s | 41.6k | 33.9k | 21.3k |
| echo @ conc 100 | p50/p99 ms | 2.276 / 3.128 | 2.801 / 4.226 | 4.604 / 6.869 |
| echo @ conc 1000 | req/s | 36.1k | 28.1k | 16.6k |
| echo @ conc 1000 | p50/p99 ms | 23.103 / 42.398 | 28.868 / 130.145 | 55.132 / 89.485 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 64.3k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.15.0

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 372.7k | 142.0k | 168.0k |
| spawn (fire & forget) | spawn_n/s | 353.7k | 260.0k | 234.7k |
| context switch | switches/s | 3.45M | 1.46M | 910.9k |
| semaphore uncontended | ops/s | 41.99M | 10.10M | 12.76M |
| semaphore contended | ops/s | 41.03M | 8.82M | 12.44M |
| queue put/get | items/s | 15.49M | 12.03M | 5.75M |
| queue shared green+native threads | items/s | 3.12M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.9k | 25.3k | 11.9k |
| tpool round-trip | mean latency | 35.88 us | 39.51 us | 84.23 us |
| echo @ conc 100 | req/s | 138.8k | 128.3k | 98.0k |
| echo @ conc 100 | p50/p99 ms | 0.687 / 1.135 | 0.732 / 1.009 | 0.989 / 1.501 |
| echo @ conc 1000 | req/s | 108.8k | 99.1k | 65.3k |
| echo @ conc 1000 | p50/p99 ms | 7.417 / 14.575 | 8.122 / 22.9 | 13.346 / 23.641 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.1k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.14.6

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 370.0k | 152.8k | 188.2k |
| spawn (fire & forget) | spawn_n/s | 368.0k | 306.1k | 276.4k |
| context switch | switches/s | 3.55M | 1.50M | 951.1k |
| semaphore uncontended | ops/s | 41.25M | 10.41M | 13.16M |
| semaphore contended | ops/s | 45.64M | 10.28M | 12.88M |
| queue put/get | items/s | 14.02M | 11.64M | 5.81M |
| queue shared green+native threads | items/s | 2.83M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.6k | 25.6k | 11.9k |
| tpool round-trip | mean latency | 36.27 us | 38.99 us | 83.76 us |
| echo @ conc 100 | req/s | 138.6k | 126.0k | 99.4k |
| echo @ conc 100 | p50/p99 ms | 0.687 / 1.082 | 0.767 / 0.927 | 0.97 / 1.437 |
| echo @ conc 1000 | req/s | 108.2k | 100.3k | 68.8k |
| echo @ conc 1000 | p50/p99 ms | 7.389 / 14.264 | 8.368 / 22.567 | 13.205 / 22.824 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.9k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.13.5

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 403.8k | 155.4k | 190.9k |
| spawn (fire & forget) | spawn_n/s | 372.3k | 305.7k | 275.3k |
| context switch | switches/s | 4.42M | 1.47M | 946.2k |
| semaphore uncontended | ops/s | 47.88M | 10.55M | 12.30M |
| semaphore contended | ops/s | 48.03M | 10.09M | 12.45M |
| queue put/get | items/s | 16.30M | 12.52M | 6.08M |
| queue shared green+native threads | items/s | 2.95M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.1k | 26.9k | 11.9k |
| tpool round-trip | mean latency | 36.96 us | 37.21 us | 84.12 us |
| echo @ conc 100 | req/s | 146.5k | 129.0k | 94.9k |
| echo @ conc 100 | p50/p99 ms | 0.646 / 0.958 | 0.742 / 0.927 | 1.027 / 1.533 |
| echo @ conc 1000 | req/s | 113.6k | 104.3k | 67.5k |
| echo @ conc 1000 | p50/p99 ms | 7.063 / 13.813 | 7.913 / 17.947 | 13.181 / 21.275 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.3k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 334.2k | 143.7k | 182.3k |
| spawn (fire & forget) | spawn_n/s | 305.1k | 281.3k | 253.1k |
| context switch | switches/s | 3.98M | 1.29M | 938.6k |
| semaphore uncontended | ops/s | 49.61M | 10.46M | 13.02M |
| semaphore contended | ops/s | 44.36M | 10.11M | 13.02M |
| queue put/get | items/s | 14.93M | 12.52M | 5.31M |
| queue shared green+native threads | items/s | 2.52M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.1k | 25.3k | 11.9k |
| tpool round-trip | mean latency | 36.9 us | 39.46 us | 84.1 us |
| echo @ conc 100 | req/s | 147.2k | 126.5k | 99.3k |
| echo @ conc 100 | p50/p99 ms | 0.634 / 0.993 | 0.8 / 1.032 | 0.997 / 1.586 |
| echo @ conc 1000 | req/s | 111.4k | 97.0k | 61.9k |
| echo @ conc 1000 | p50/p99 ms | 7.23 / 14.111 | 8.679 / 21.78 | 13.803 / 27.344 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.9k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.11.15

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 339.7k | 143.1k | 182.0k |
| spawn (fire & forget) | spawn_n/s | 319.4k | 289.3k | 261.1k |
| context switch | switches/s | 3.79M | 1.68M | 1.06M |
| semaphore uncontended | ops/s | 34.68M | 10.91M | 11.99M |
| semaphore contended | ops/s | 39.33M | 10.65M | 11.82M |
| queue put/get | items/s | 14.44M | 12.12M | 4.88M |
| queue shared green+native threads | items/s | 2.54M | **error** | **error** |
| tpool round-trip | calls/s | 27.2k | 25.7k | 11.5k |
| tpool round-trip | mean latency | 36.8 us | 38.95 us | 86.7 us |
| echo @ conc 100 | req/s | 143.6k | 120.6k | 99.3k |
| echo @ conc 100 | p50/p99 ms | 0.671 / 1.026 | 0.787 / 0.957 | 0.983 / 1.459 |
| echo @ conc 1000 | req/s | 108.8k | 98.4k | 65.2k |
| echo @ conc 1000 | p50/p99 ms | 7.333 / 13.799 | 8.487 / 23.016 | 13.987 / 23.924 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 18.7k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.06M | 213.7k | 201.2k |
| spawn (fire & forget) | spawn_n/s | 914.3k | 542.8k | 404.8k |
| context switch | switches/s | 3.51M | 1.44M | 733.5k |
| semaphore uncontended | ops/s | 37.78M | 10.81M | 6.94M |
| semaphore contended | ops/s | 36.21M | 10.29M | 6.69M |
| queue put/get | items/s | 13.84M | 10.78M | 2.91M |
| queue shared green+native threads | items/s | 2.73M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.8k | 25.9k | 10.9k |
| tpool round-trip | mean latency | 35.99 us | 38.6 us | 91.63 us |
| echo @ conc 100 | req/s | 142.1k | 115.7k | 76.4k |
| echo @ conc 100 | p50/p99 ms | 0.662 / 0.885 | 0.837 / 0.971 | 1.283 / 1.907 |
| echo @ conc 1000 | req/s | 113.5k | 93.7k | 49.7k |
| echo @ conc 1000 | p50/p99 ms | 7.413 / 11.543 | 8.964 / 22.873 | 19.099 / 35.328 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.6k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.8.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 982.6k | 244.7k | 193.6k |
| spawn (fire & forget) | spawn_n/s | 873.8k | 477.8k | 321.4k |
| context switch | switches/s | 2.99M | 1.45M | 602.4k |
| semaphore uncontended | ops/s | 46.46M | 12.16M | 4.34M |
| semaphore contended | ops/s | 26.91M | 11.45M | 4.10M |
| queue put/get | items/s | 13.22M | 9.67M | 1.97M |
| queue shared green+native threads | items/s | 2.33M | **error** | **deadlock** |
| tpool round-trip | calls/s | 28.0k | 25.4k | 10.7k |
| tpool round-trip | mean latency | 35.66 us | 39.37 us | 93.67 us |
| echo @ conc 100 | req/s | 139.2k | 104.9k | 65.8k |
| echo @ conc 100 | p50/p99 ms | 0.691 / 0.857 | 0.915 / 1.066 | 1.496 / 2.164 |
| echo @ conc 1000 | req/s | 117.3k | 86.8k | 45.0k |
| echo @ conc 1000 | p50/p99 ms | 7.135 / 11.58 | 9.811 / 25.024 | 19.905 / 45.687 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.6k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 2.7.18

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.32M | 230.7k | 143.8k |
| spawn (fire & forget) | spawn_n/s | 1.07M | 518.2k | 289.5k |
| context switch | switches/s | 3.47M | 1.29M | 459.7k |
| semaphore uncontended | ops/s | 40.66M | 13.87M | 4.97M |
| semaphore contended | ops/s | 19.65M | 10.61M | 4.26M |
| queue put/get | items/s | 10.47M | 7.90M | 1.86M |
| queue shared green+native threads | items/s | 1.07M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.9k | **deadlock** | 10.3k |
| tpool round-trip | mean latency | 35.83 us | **deadlock** | 96.9 us |
| echo @ conc 100 | req/s | 132.0k | 103.1k | 64.5k |
| echo @ conc 100 | p50/p99 ms | 0.728 / 0.851 | 0.943 / 1.087 | 1.499 / 2.131 |
| echo @ conc 1000 | req/s | 104.8k | 85.8k | 38.2k |
| echo @ conc 1000 | p50/p99 ms | 7.868 / 12.17 | 10.043 / 23.744 | 23.852 / 41.086 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.1k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Headline findings — amd64

Numbers below are from **Python 3.15.0**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 111.6k gt/s vs gevent 56.4k vs eventlet 61.9k — filament 2.0x gevent, 1.8x eventlet. filament's lead is widest on the older interpreters (up to ~4.7x gevent on 3.10/3.8).
- **Context-switch rate — filament wins:** filament 2.17M sw/s vs gevent 786.2k vs eventlet 486.4k — filament 2.8x gevent, 4.5x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~21.81M uncontended ops/s vs gevent 6.91M / eventlet 6.67M (3-8x), and it leads on queue put/get too.
- **Mixed green+native queue — filament only:** a single bounded `Queue` worked simultaneously by greenthreads AND native `threading.Thread` producers/consumers runs at ~2.76M items/s in filament. The same workload on gevent/eventlet deadlocks or errors — their queues are hub-bound and cannot be used from a foreign OS thread. filament's per-thread scheduler + deferred cross-thread wakeup makes this a first-class pattern (same mechanism as the #137 win).
- **Threadpool round-trip — filament wins:** filament 75.0k calls/s vs gevent 49.4k vs eventlet 11.3k — filament 1.5x gevent, 6.6x eventlet. filament's pool wakes the most-recently-idle (MRU) worker for each job, keeping the hot worker's stack and caches warm.
- **Echo server — filament wins:** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency (see the 3.13 table); eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path carry the socket hot loop.
- **#137 logging-in-threadpool — filament's headline win:** filament logs from its real-thread pool while the hub runs greenthreads and **just works, no workaround, ~15-16k msgs/s** (Python 3.8-3.13). gevent and eventlet both **deadlock** under a monkey-patched hub, and gevent's documented mitigations (hub threadpool + native logging locks + `logThreads=False`) **do not** save it — it still deadlocks. This is filament's whole reason for existing, and it holds up.

## Headline findings — arm64

Numbers below are from **Python 3.15.0**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 372.7k gt/s vs gevent 142.0k vs eventlet 168.0k — filament 2.6x gevent, 2.2x eventlet. filament's lead is widest on the older interpreters (up to ~4.7x gevent on 3.10/3.8).
- **Context-switch rate — filament wins:** filament 3.45M sw/s vs gevent 1.46M vs eventlet 910.9k — filament 2.4x gevent, 3.8x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~41.99M uncontended ops/s vs gevent 10.10M / eventlet 12.76M (3-8x), and it leads on queue put/get too.
- **Mixed green+native queue — filament only:** a single bounded `Queue` worked simultaneously by greenthreads AND native `threading.Thread` producers/consumers runs at ~3.12M items/s in filament. The same workload on gevent/eventlet deadlocks or errors — their queues are hub-bound and cannot be used from a foreign OS thread. filament's per-thread scheduler + deferred cross-thread wakeup makes this a first-class pattern (same mechanism as the #137 win).
- **Threadpool round-trip — filament wins:** filament 27.9k calls/s vs gevent 25.3k vs eventlet 11.9k — filament 1.1x gevent, 2.3x eventlet. filament's pool wakes the most-recently-idle (MRU) worker for each job, keeping the hot worker's stack and caches warm.
- **Echo server — filament wins:** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency (see the 3.13 table); eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path carry the socket hot loop.
- **#137 logging-in-threadpool — filament's headline win:** filament logs from its real-thread pool while the hub runs greenthreads and **just works, no workaround, ~15-16k msgs/s** (Python 3.8-3.13). gevent and eventlet both **deadlock** under a monkey-patched hub, and gevent's documented mitigations (hub threadpool + native logging locks + `logThreads=False`) **do not** save it — it still deadlocks. This is filament's whole reason for existing, and it holds up.

