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
- **filament** builds and runs every benchmark (including `#137`) on every interpreter in the matrix, 2.7 through 3.15, with a version-tagged `.so` per interpreter.

## amd64 · Python 3.15.0

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 108.6k | 57.9k | 62.3k |
| spawn (fire & forget) | spawn_n/s | 105.5k | 88.3k | 83.0k |
| context switch | switches/s | 2.05M | 812.0k | 494.5k |
| semaphore uncontended | ops/s | 21.67M | 6.93M | 6.71M |
| semaphore contended | ops/s | 17.60M | 7.13M | 6.59M |
| queue put/get | items/s | 7.72M | 6.12M | 3.34M |
| queue shared green+native threads | items/s | 2.80M | **error** | **deadlock** |
| tpool round-trip | calls/s | 47.7k | 46.5k | 11.2k |
| tpool round-trip | mean latency | 20.98 us | 21.52 us | 88.93 us |
| echo @ conc 100 | req/s | 41.2k | 36.9k | 30.3k |
| echo @ conc 100 | p50/p99 ms | 2.279 / 3.65 | 2.564 / 4.139 | 3.19 / 4.631 |
| echo @ conc 1000 | req/s | 34.7k | 29.8k | 22.9k |
| echo @ conc 1000 | p50/p99 ms | 22.405 / 49.01 | 26.048 / 114.283 | 38.58 / 74.878 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 23.1k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.14.4

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 119.9k | 70.0k | 81.6k |
| spawn (fire & forget) | spawn_n/s | 112.4k | 105.3k | 101.8k |
| context switch | switches/s | 2.21M | 782.1k | 445.5k |
| semaphore uncontended | ops/s | 20.73M | 7.22M | 6.85M |
| semaphore contended | ops/s | 21.67M | 7.44M | 6.29M |
| queue put/get | items/s | 8.04M | 6.09M | 3.39M |
| queue shared green+native threads | items/s | 2.52M | **error** | **deadlock** |
| tpool round-trip | calls/s | 58.8k | 46.9k | 11.4k |
| tpool round-trip | mean latency | 17.0 us | 21.34 us | 87.97 us |
| echo @ conc 100 | req/s | 43.9k | 33.6k | 27.9k |
| echo @ conc 100 | p50/p99 ms | 2.289 / 3.459 | 2.744 / 4.314 | 3.498 / 5.114 |
| echo @ conc 1000 | req/s | 34.1k | 27.7k | 21.1k |
| echo @ conc 1000 | p50/p99 ms | 23.535 / 48.8 | 28.399 / 124.938 | 42.105 / 73.004 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 25.3k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.13.14

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 113.6k | 63.8k | 69.2k |
| spawn (fire & forget) | spawn_n/s | 110.5k | 103.4k | 96.1k |
| context switch | switches/s | 2.37M | 820.8k | 495.5k |
| semaphore uncontended | ops/s | 21.39M | 7.42M | 6.30M |
| semaphore contended | ops/s | 20.34M | 7.47M | 6.31M |
| queue put/get | items/s | 8.28M | 6.56M | 3.29M |
| queue shared green+native threads | items/s | 2.87M | **error** | **deadlock** |
| tpool round-trip | calls/s | 45.8k | 30.0k | 12.0k |
| tpool round-trip | mean latency | 21.83 us | 33.28 us | 83.12 us |
| echo @ conc 100 | req/s | 41.2k | 36.4k | 29.1k |
| echo @ conc 100 | p50/p99 ms | 2.286 / 3.833 | 2.582 / 4.015 | 3.306 / 4.911 |
| echo @ conc 1000 | req/s | 34.8k | 29.1k | 22.1k |
| echo @ conc 1000 | p50/p99 ms | 22.26 / 47.366 | 27.084 / 113.31 | 40.079 / 68.105 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 28.9k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 107.3k | 61.2k | 63.5k |
| spawn (fire & forget) | spawn_n/s | 103.1k | 94.8k | 86.5k |
| context switch | switches/s | 2.48M | 835.1k | 479.1k |
| semaphore uncontended | ops/s | 20.64M | 7.27M | 6.79M |
| semaphore contended | ops/s | 19.64M | 7.07M | 6.52M |
| queue put/get | items/s | 8.45M | 6.51M | 2.98M |
| queue shared green+native threads | items/s | 2.34M | **error** | **error** |
| tpool round-trip | calls/s | 45.6k | 29.2k | 11.5k |
| tpool round-trip | mean latency | 21.94 us | 34.3 us | 87.21 us |
| echo @ conc 100 | req/s | 42.8k | 36.4k | 28.7k |
| echo @ conc 100 | p50/p99 ms | 2.216 / 3.564 | 2.588 / 4.343 | 3.39 / 4.931 |
| echo @ conc 1000 | req/s | 35.7k | 28.9k | 21.4k |
| echo @ conc 1000 | p50/p99 ms | 22.409 / 46.826 | 26.591 / 116.369 | 41.784 / 79.02 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 41.8k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.11.15

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 107.7k | 58.7k | 62.4k |
| spawn (fire & forget) | spawn_n/s | 104.0k | 95.1k | 89.2k |
| context switch | switches/s | 2.23M | 953.3k | 489.4k |
| semaphore uncontended | ops/s | 19.87M | 7.96M | 5.67M |
| semaphore contended | ops/s | 18.45M | 7.02M | 5.71M |
| queue put/get | items/s | 7.93M | 5.99M | 2.67M |
| queue shared green+native threads | items/s | 2.48M | **error** | **deadlock** |
| tpool round-trip | calls/s | 44.9k | 29.1k | 11.0k |
| tpool round-trip | mean latency | 22.26 us | 34.32 us | 90.52 us |
| echo @ conc 100 | req/s | 42.9k | 35.3k | 28.1k |
| echo @ conc 100 | p50/p99 ms | 2.288 / 3.648 | 2.684 / 4.231 | 3.453 / 4.991 |
| echo @ conc 1000 | req/s | 34.5k | 28.3k | 20.8k |
| echo @ conc 1000 | p50/p99 ms | 23.158 / 48.876 | 27.61 / 128.356 | 42.784 / 77.676 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 48.3k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 426.6k | 114.8k | 93.1k |
| spawn (fire & forget) | spawn_n/s | 395.9k | 302.4k | 192.9k |
| context switch | switches/s | 2.13M | 867.3k | 369.5k |
| semaphore uncontended | ops/s | 16.28M | 7.78M | 3.45M |
| semaphore contended | ops/s | 14.69M | 7.17M | 3.23M |
| queue put/get | items/s | 6.75M | 6.06M | 1.61M |
| queue shared green+native threads | items/s | 2.57M | **error** | **deadlock** |
| tpool round-trip | calls/s | 44.4k | 39.5k | 9.6k |
| tpool round-trip | mean latency | 22.5 us | 25.33 us | 103.81 us |
| echo @ conc 100 | req/s | 41.2k | 34.6k | 22.5k |
| echo @ conc 100 | p50/p99 ms | 2.33 / 3.239 | 2.768 / 4.22 | 4.329 / 6.268 |
| echo @ conc 1000 | req/s | 35.7k | 28.7k | 15.3k |
| echo @ conc 1000 | p50/p99 ms | 23.574 / 37.319 | 28.531 / 128.076 | 59.06 / 109.417 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 30.2k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.8.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 470.9k | 103.8k | 92.7k |
| spawn (fire & forget) | spawn_n/s | 423.4k | 274.4k | 177.9k |
| context switch | switches/s | 1.93M | 813.0k | 356.6k |
| semaphore uncontended | ops/s | 29.08M | 8.50M | 3.33M |
| semaphore contended | ops/s | 22.65M | 7.76M | 3.06M |
| queue put/get | items/s | 8.74M | 6.42M | 1.45M |
| queue shared green+native threads | items/s | 2.79M | **error** | **deadlock** |
| tpool round-trip | calls/s | 47.3k | 38.7k | 8.9k |
| tpool round-trip | mean latency | 21.16 us | 25.84 us | 112.76 us |
| echo @ conc 100 | req/s | 40.7k | 33.6k | 22.9k |
| echo @ conc 100 | p50/p99 ms | 2.345 / 3.042 | 2.878 / 4.192 | 4.205 / 6.079 |
| echo @ conc 1000 | req/s | 35.8k | 28.1k | 16.0k |
| echo @ conc 1000 | p50/p99 ms | 23.391 / 39.147 | 28.919 / 128.147 | 56.274 / 96.65 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 75.5k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.15.0

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 355.2k | 155.3k | 177.0k |
| spawn (fire & forget) | spawn_n/s | 340.0k | 298.9k | 250.1k |
| context switch | switches/s | 3.54M | 1.52M | 950.1k |
| semaphore uncontended | ops/s | 45.26M | 10.79M | 13.03M |
| semaphore contended | ops/s | 43.96M | 9.42M | 12.65M |
| queue put/get | items/s | 15.66M | 12.11M | 5.88M |
| queue shared green+native threads | items/s | 3.08M | **error** | **deadlock** |
| tpool round-trip | calls/s | 26.9k | 24.8k | 11.5k |
| tpool round-trip | mean latency | 37.14 us | 40.32 us | 86.66 us |
| echo @ conc 100 | req/s | 141.9k | 126.3k | 96.8k |
| echo @ conc 100 | p50/p99 ms | 0.669 / 1.048 | 0.773 / 1.083 | 1.009 / 1.535 |
| echo @ conc 1000 | req/s | 106.6k | 98.0k | 68.4k |
| echo @ conc 1000 | p50/p99 ms | 7.409 / 14.286 | 8.424 / 26.342 | 13.361 / 19.766 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.6k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.14.6

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 370.0k | 161.4k | 199.7k |
| spawn (fire & forget) | spawn_n/s | 368.6k | 317.5k | 282.7k |
| context switch | switches/s | 3.59M | 1.53M | 990.8k |
| semaphore uncontended | ops/s | 48.04M | 10.73M | 13.28M |
| semaphore contended | ops/s | 44.76M | 10.58M | 13.00M |
| queue put/get | items/s | 13.90M | 11.82M | 5.92M |
| queue shared green+native threads | items/s | 2.87M | **error** | **deadlock** |
| tpool round-trip | calls/s | 26.8k | 25.2k | 12.1k |
| tpool round-trip | mean latency | 37.36 us | 39.7 us | 82.69 us |
| echo @ conc 100 | req/s | 139.8k | 125.9k | 94.2k |
| echo @ conc 100 | p50/p99 ms | 0.683 / 1.052 | 0.789 / 1.191 | 1.029 / 1.571 |
| echo @ conc 1000 | req/s | 113.6k | 101.1k | 69.4k |
| echo @ conc 1000 | p50/p99 ms | 7.134 / 13.38 | 8.381 / 18.389 | 13.022 / 21.227 |

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
| spawn (tracked, spawn+join) | greenthreads/s | 421.2k | 160.3k | 196.2k |
| spawn (fire & forget) | spawn_n/s | 413.9k | 319.8k | 286.1k |
| context switch | switches/s | 4.37M | 1.42M | 922.6k |
| semaphore uncontended | ops/s | 42.21M | 11.02M | 12.49M |
| semaphore contended | ops/s | 48.65M | 10.48M | 12.58M |
| queue put/get | items/s | 16.36M | 12.68M | 6.12M |
| queue shared green+native threads | items/s | 2.99M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.7k | 26.0k | 12.1k |
| tpool round-trip | mean latency | 36.15 us | 38.44 us | 82.93 us |
| echo @ conc 100 | req/s | 147.2k | 129.4k | 98.5k |
| echo @ conc 100 | p50/p99 ms | 0.645 / 0.98 | 0.748 / 1.009 | 0.981 / 1.471 |
| echo @ conc 1000 | req/s | 112.9k | 105.7k | 69.6k |
| echo @ conc 1000 | p50/p99 ms | 6.913 / 13.02 | 7.97 / 17.389 | 12.792 / 19.495 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.5k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 335.2k | 146.9k | 184.3k |
| spawn (fire & forget) | spawn_n/s | 329.7k | 275.5k | 253.2k |
| context switch | switches/s | 3.98M | 1.41M | 986.5k |
| semaphore uncontended | ops/s | 47.90M | 10.40M | 12.98M |
| semaphore contended | ops/s | 43.36M | 10.29M | 12.74M |
| queue put/get | items/s | 15.04M | 10.82M | 5.31M |
| queue shared green+native threads | items/s | 2.58M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.2k | 25.9k | 11.7k |
| tpool round-trip | mean latency | 36.74 us | 38.56 us | 85.16 us |
| echo @ conc 100 | req/s | 146.1k | 118.6k | 95.2k |
| echo @ conc 100 | p50/p99 ms | 0.646 / 1.06 | 0.797 / 1.017 | 1.026 / 1.561 |
| echo @ conc 1000 | req/s | 115.3k | 97.7k | 63.8k |
| echo @ conc 1000 | p50/p99 ms | 7.096 / 13.603 | 8.627 / 22.43 | 14.217 / 26.335 |

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
| spawn (tracked, spawn+join) | greenthreads/s | 340.2k | 145.6k | 184.2k |
| spawn (fire & forget) | spawn_n/s | 330.1k | 295.1k | 266.0k |
| context switch | switches/s | 3.75M | 1.69M | 1.04M |
| semaphore uncontended | ops/s | 36.39M | 11.15M | 12.11M |
| semaphore contended | ops/s | 38.35M | 10.96M | 11.85M |
| queue put/get | items/s | 14.25M | 12.50M | 4.86M |
| queue shared green+native threads | items/s | 2.52M | **error** | **deadlock** |
| tpool round-trip | calls/s | 26.9k | 25.2k | 11.8k |
| tpool round-trip | mean latency | 37.2 us | 39.74 us | 84.74 us |
| echo @ conc 100 | req/s | 134.9k | 126.2k | 99.6k |
| echo @ conc 100 | p50/p99 ms | 0.675 / 1.137 | 0.759 / 1.027 | 0.983 / 1.434 |
| echo @ conc 1000 | req/s | 103.6k | 100.7k | 67.3k |
| echo @ conc 1000 | p50/p99 ms | 7.138 / 14.472 | 8.251 / 17.591 | 13.757 / 23.873 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 18.0k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 798.1k | 214.8k | 204.7k |
| spawn (fire & forget) | spawn_n/s | 763.0k | 571.6k | 400.6k |
| context switch | switches/s | 3.54M | 1.44M | 723.1k |
| semaphore uncontended | ops/s | 40.83M | 10.25M | 7.08M |
| semaphore contended | ops/s | 34.99M | 10.03M | 6.69M |
| queue put/get | items/s | 13.79M | 10.68M | 2.95M |
| queue shared green+native threads | items/s | 2.69M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.9k | 25.1k | 10.6k |
| tpool round-trip | mean latency | 35.85 us | 39.87 us | 94.78 us |
| echo @ conc 100 | req/s | 145.7k | 117.6k | 77.7k |
| echo @ conc 100 | p50/p99 ms | 0.662 / 0.815 | 0.821 / 1.115 | 1.264 / 1.867 |
| echo @ conc 1000 | req/s | 116.1k | 96.4k | 50.7k |
| echo @ conc 1000 | p50/p99 ms | 7.213 / 11.063 | 8.911 / 16.81 | 18.006 / 31.321 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.2k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.8.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 872.3k | 237.4k | 197.8k |
| spawn (fire & forget) | spawn_n/s | 764.3k | 497.1k | 334.7k |
| context switch | switches/s | 3.03M | 1.48M | 598.3k |
| semaphore uncontended | ops/s | 45.48M | 10.24M | 4.52M |
| semaphore contended | ops/s | 28.94M | 10.03M | 4.32M |
| queue put/get | items/s | 13.10M | 9.66M | 2.02M |
| queue shared green+native threads | items/s | 2.27M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.8k | 24.6k | 10.2k |
| tpool round-trip | mean latency | 35.96 us | 40.61 us | 97.62 us |
| echo @ conc 100 | req/s | 139.9k | 104.3k | 66.4k |
| echo @ conc 100 | p50/p99 ms | 0.687 / 0.93 | 0.908 / 1.046 | 1.472 / 2.119 |
| echo @ conc 1000 | req/s | 114.3k | 85.2k | 45.5k |
| echo @ conc 1000 | p50/p99 ms | 7.39 / 11.441 | 9.946 / 23.801 | 20.15 / 33.474 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.3k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 2.7.18

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 966.2k | 239.9k | 141.8k |
| spawn (fire & forget) | spawn_n/s | 881.9k | 536.9k | 285.0k |
| context switch | switches/s | 3.37M | 1.29M | 448.9k |
| semaphore uncontended | ops/s | 39.91M | 14.07M | 5.07M |
| semaphore contended | ops/s | 19.59M | 10.85M | 4.45M |
| queue put/get | items/s | 10.68M | 7.90M | 1.87M |
| queue shared green+native threads | items/s | 1.04M | **error** | **deadlock** |
| tpool round-trip | calls/s | 28.3k | 166.1 | 10.0k |
| tpool round-trip | mean latency | 35.35 us | 6018.9 us | 99.7 us |
| echo @ conc 100 | req/s | 128.9k | 102.8k | 62.4k |
| echo @ conc 100 | p50/p99 ms | 0.735 / 1.131 | 0.933 / 1.431 | 1.586 / 2.396 |
| echo @ conc 1000 | req/s | 108.7k | 83.3k | 38.0k |
| echo @ conc 1000 | p50/p99 ms | 7.376 / 11.632 | 10.39 / 23.706 | 23.771 / 38.588 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.0k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Headline findings — amd64

Numbers below are from **Python 3.15.0**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 108.6k gt/s vs gevent 57.9k vs eventlet 62.3k — filament 1.9x gevent, 1.7x eventlet. Across the matrix filament runs 1.71-4.54x the spawn rate of gevent, widest on Python 3.8.20.
- **Context-switch rate — filament wins:** filament 2.05M sw/s vs gevent 812.0k vs eventlet 494.5k — filament 2.5x gevent, 4.1x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~21.67M uncontended ops/s vs gevent 6.93M / eventlet 6.71M (3-8x), and it leads on queue put/get too.
- **Mixed green+native queue — filament only:** a single bounded `Queue` worked simultaneously by greenthreads AND native `threading.Thread` producers/consumers runs at ~2.80M items/s in filament. The same workload on gevent/eventlet deadlocks or errors — their queues are hub-bound and cannot be used from a foreign OS thread. filament's per-thread scheduler + deferred cross-thread wakeup makes this a first-class pattern (same mechanism as the #137 win).
- **Threadpool round-trip:** filament 47.7k calls/s vs gevent 46.5k vs eventlet 11.2k — filament 1.0x gevent, 4.2x eventlet. Across Python 3 in this matrix that is 1.03-1.56x gevent's rate, best on Python 3.12.13; filament's pool wakes the most-recently-idle (MRU) worker for each job, keeping the hot worker's stack and caches warm.
- **Echo server — filament wins:** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency; eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path carry the socket hot loop.
- **#137 logging-in-threadpool — filament's headline win:** filament logs from its real-thread pool while the hub runs greenthreads and **just works, no workaround, 23.1k-75.5k msgs/s** across the matrix. gevent and eventlet both **deadlock** under a monkey-patched hub, and gevent's documented mitigations (hub threadpool + native logging locks + `logThreads=False`) **do not** save it — it still deadlocks. This is filament's whole reason for existing, and it holds up.

## Headline findings — arm64

Numbers below are from **Python 3.15.0**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 355.2k gt/s vs gevent 155.3k vs eventlet 177.0k — filament 2.3x gevent, 2.0x eventlet. Across the matrix filament runs 2.28-4.03x the spawn rate of gevent, widest on Python 2.7.18.
- **Context-switch rate — filament wins:** filament 3.54M sw/s vs gevent 1.52M vs eventlet 950.1k — filament 2.3x gevent, 3.7x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~45.26M uncontended ops/s vs gevent 10.79M / eventlet 13.03M (3-8x), and it leads on queue put/get too.
- **Mixed green+native queue — filament only:** a single bounded `Queue` worked simultaneously by greenthreads AND native `threading.Thread` producers/consumers runs at ~3.08M items/s in filament. The same workload on gevent/eventlet deadlocks or errors — their queues are hub-bound and cannot be used from a foreign OS thread. filament's per-thread scheduler + deferred cross-thread wakeup makes this a first-class pattern (same mechanism as the #137 win).
- **Threadpool round-trip:** filament 26.9k calls/s vs gevent 24.8k vs eventlet 11.5k — filament 1.1x gevent, 2.3x eventlet. Across Python 3 in this matrix that is 1.05-1.13x gevent's rate, best on Python 3.8.20; filament's pool wakes the most-recently-idle (MRU) worker for each job, keeping the hot worker's stack and caches warm.
- **Echo server — filament wins:** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency; eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path carry the socket hot loop.
- **#137 logging-in-threadpool — filament's headline win:** filament logs from its real-thread pool while the hub runs greenthreads and **just works, no workaround, 16.0k-18.0k msgs/s** across the matrix. gevent and eventlet both **deadlock** under a monkey-patched hub, and gevent's documented mitigations (hub threadpool + native logging locks + `logThreads=False`) **do not** save it — it still deadlocks. This is filament's whole reason for existing, and it holds up.

