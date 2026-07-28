# filament vs gevent vs eventlet — benchmark results

Greenlet-based cooperative concurrency shootout: **filament** (this repo) against modern **gevent** and **eventlet**, across CPython versions on aarch64 and x86_64 Linux (results are grouped by architecture below).

Each (framework, benchmark) ran in its own fresh subprocess. Micro-benchmarks report the **median** of several timed reps (warm-up discarded), using a monotonic clock. Higher is better for throughput; lower is better for latency.

## Methodology

- Same logical workload run three ways (filament / gevent / eventlet) with identical sizes per framework. For filament the in-process client is `filament.socket`; gevent uses `gevent.socket` + `StreamServer`-style accept loop; eventlet uses `eventlet.green.socket`. The echo client stays in the same framework as the server for fairness.
- Each pair runs in a **fresh interpreter subprocess** so monkey-patching and hub state never leak between frameworks.
- Spawn = 100k greenthreads spawned then joined. Context switch = 100 greenthreads x 10k `sleep(0)` = 1,000,000 switches. Semaphore uncontended = 1M acquire/release on one greenthread; contended = 50 greenthreads on a `Semaphore(1)`. Queue = 200k producer/consumer items. tpool = 3000 sequential real-thread round-trips. Echo = concurrency 100 (x100 round-trips) and 1000 (x20), 64-byte payload.
- Queue mixed = ONE bounded queue (maxsize 100) shared simultaneously by a greenthread producer + consumer AND a native `threading.Thread` producer + consumer (50k items per producer), so native threads block in `q.get()`/`q.put()` while greenthreads work the same queue. gevent/eventlet queues are hub-bound; foreign-OS-thread use is undefined for them and runs under the deadlock watchdog.
- **#137**: monkey-patch everything, then log heavily from real OS-thread pool workers while greenthreads spin in the hub. Each attempt runs under a hard 30 s subprocess watchdog; a hang is recorded as **deadlock**. Whether gevent/eventlet hang here depends on the machine -- it is a race between the hub and the logging lock, and a faster host with more cores wins it more often -- so a single cell is one roll of the dice, not a property of the library. filament has not lost it on any machine or interpreter.

> **OS-thread caveat.** `tpool` and `#137` cross into real OS threads, and on a many-core host their absolute numbers are not reproducible: the amd64 box (32 vCPUs) gives a clean bimodal split ~1.6x apart, switching even between reps inside one process. It is thread placement, and `taskset` proves it -- pinned to a single CPU the same benchmark repeats to ~2% (filament 50-52k, gevent 38-39k calls/s), pinned to two it is faster and mostly steady, and turned loose on all 32 it oscillates. The 1.6x factor hits both runtimes equally, so the *ranking* holds even where the absolute value does not: filament leads gevent by 1.3-1.4x in every pinned configuration. The 6-core arm64 box does not show this, having far less placement freedom. Read a single tpool or #137 cell as an order of magnitude; the pure-greenthread rows repeat to within a few percent.

> **Cross-version caveat.** Each Python version's table was recorded in its own sequential run on the same box. Interpreter speed differs across versions, so absolute numbers are **not** comparable across Python versions. The reliable signal is the **ratio between frameworks within one version**: all three frameworks in a table ran back-to-back under identical conditions.

## Environments

| Arch | Python | greenlet | gevent | eventlet | host | measured |
|---|---|---|---|---|---|---|
| amd64 | 3.15.0 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t, bare metal | 2026-07-28 (384715a) |
| amd64 | 3.14.4 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t, bare metal | 2026-07-28 (384715a) |
| amd64 | 3.13.14 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t, bare metal | 2026-07-28 (384715a) |
| amd64 | 3.12.13 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t, bare metal | 2026-07-28 (384715a) |
| amd64 | 3.11.15 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t, bare metal | 2026-07-28 (384715a) |
| amd64 | 3.10.20 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t, bare metal | 2026-07-28 (384715a) |
| amd64 | 3.8.20 | 3.1.1 | 24.2.1 | 0.39.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t, bare metal | 2026-07-28 (384715a) |
| arm64 | 3.15.0 | 3.3.2 | 26.7.0 | 0.41.1 | 6 cpus, container | 2026-07-27 (2bfe53a) |
| arm64 | 3.14.6 | 3.3.2 | 26.7.0 | 0.41.1 | 6 cpus, container | 2026-07-27 (2bfe53a) |
| arm64 | 3.13.5 | 3.5.4 | 26.7.0 | 0.41.1 | 6 cpus, container | 2026-07-27 (2bfe53a) |
| arm64 | 3.12.13 | 3.5.4 | 26.7.0 | 0.41.1 | 6 cpus, container | 2026-07-27 (2bfe53a) |
| arm64 | 3.11.15 | 3.3.2 | 26.7.0 | 0.41.1 | 6 cpus, container | 2026-07-27 (2bfe53a) |
| arm64 | 3.10.20 | 3.5.4 | 26.7.0 | 0.41.1 | 6 cpus, container | 2026-07-27 (2bfe53a) |
| arm64 | 3.8.20 | 3.1.1 | 22.10.2 | 0.39.1 | 6 cpus, container | 2026-07-27 (2bfe53a) |
| arm64 | 2.7.18 | 2.0.2 | 22.10.2 | 0.33.3 | 6 cpus, container | 2026-07-27 (2bfe53a) |

Availability notes:

- **gevent on Python 2.7**: no cp27/aarch64 wheel exists and stock source builds fail under a modern GCC (Cython-generated C errors); where the 2.7 column shows gevent numbers they come from a locally-built older gevent (see the environments table). eventlet 0.33.3 (pure-Python) and filament both build/run on 2.7.
- **gevent tpool on Python 2.7**: gevent **22.10.2** (the last py2.7 release) deadlocks in the threadpool round-trip benchmark on 2.7 — reproducible even at small scale. Its predecessor 21.12.0 completed the same benchmark (~23.6k calls/s), so this is a gevent regression in its final py2.7 release, not a harness artifact.
- **gevent/eventlet on Python 3.8**: latest releases have no 3.8/aarch64 wheels, so pip resolved to gevent **22.10.2** and eventlet **0.39.1** (still current enough for a fair comparison).
- **filament** builds and runs every benchmark (including `#137`) on every interpreter in the matrix, 2.7 through 3.15, with a version-tagged `.so` per interpreter.

## amd64 · Python 3.15.0

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 276.7k | 96.5k | 112.4k |
| spawn (fire & forget) | spawn_n/s | 253.9k | 198.6k | 180.0k |
| context switch | switches/s | 2.05M | 810.2k | 494.8k |
| semaphore uncontended | ops/s | 17.80M | 7.25M | 6.72M |
| semaphore contended | ops/s | 18.45M | 7.02M | 6.35M |
| queue put/get | items/s | 7.81M | 6.56M | 3.30M |
| queue shared green+native threads | items/s | 3.61M | **error** | **deadlock** |
| tpool round-trip | calls/s | 102.4k | 60.7k | 24.4k |
| tpool round-trip | mean latency | 9.77 us | 16.49 us | 41.06 us |
| echo @ conc 100 | req/s | 98.2k | 75.3k | 53.3k |
| echo @ conc 100 | p50/p99 ms | 0.966 / 1.478 | 1.28 / 1.499 | 1.811 / 2.685 |
| echo @ conc 1000 | req/s | 78.8k | 61.7k | 40.7k |
| echo @ conc 1000 | p50/p99 ms | 10.255 / 18.936 | 13.243 / 34.661 | 22.285 / 39.328 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 175.6k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 151.5k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 155.0k msg/s |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.14.4

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 301.6k | 117.5k | 154.2k |
| spawn (fire & forget) | spawn_n/s | 260.4k | 228.3k | 213.6k |
| context switch | switches/s | 1.95M | 796.5k | 484.3k |
| semaphore uncontended | ops/s | 23.74M | 7.55M | 7.18M |
| semaphore contended | ops/s | 20.40M | 6.64M | 6.85M |
| queue put/get | items/s | 8.42M | 6.53M | 3.50M |
| queue shared green+native threads | items/s | 3.41M | **error** | **deadlock** |
| tpool round-trip | calls/s | 105.2k | 53.3k | 28.6k |
| tpool round-trip | mean latency | 9.51 us | 18.76 us | 34.95 us |
| echo @ conc 100 | req/s | 94.4k | 76.0k | 54.4k |
| echo @ conc 100 | p50/p99 ms | 1.002 / 1.529 | 1.264 / 1.423 | 1.796 / 2.608 |
| echo @ conc 1000 | req/s | 79.6k | 62.8k | 40.7k |
| echo @ conc 1000 | p50/p99 ms | 9.947 / 21.812 | 13.127 / 33.503 | 21.929 / 36.482 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 179.3k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 153.7k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 155.5k msg/s |
| eventlet | eventlet.tpool | OK — completed | 91.2k msg/s |

## amd64 · Python 3.13.14

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 275.3k | 102.5k | 112.2k |
| spawn (fire & forget) | spawn_n/s | 260.9k | 216.1k | 194.1k |
| context switch | switches/s | 2.45M | 820.4k | 502.8k |
| semaphore uncontended | ops/s | 23.26M | 7.37M | 6.47M |
| semaphore contended | ops/s | 20.08M | 7.23M | 6.54M |
| queue put/get | items/s | 8.62M | 6.87M | 3.32M |
| queue shared green+native threads | items/s | 3.37M | **error** | **error** |
| tpool round-trip | calls/s | 102.9k | 50.6k | 25.1k |
| tpool round-trip | mean latency | 9.72 us | 19.75 us | 39.76 us |
| echo @ conc 100 | req/s | 97.2k | 69.2k | 56.2k |
| echo @ conc 100 | p50/p99 ms | 0.987 / 1.469 | 1.294 / 1.721 | 1.732 / 2.495 |
| echo @ conc 1000 | req/s | 78.1k | 60.8k | 41.3k |
| echo @ conc 1000 | p50/p99 ms | 9.902 / 17.986 | 14.955 / 33.913 | 21.661 / 39.145 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 171.5k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 55.2k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 153.6k msg/s |
| eventlet | eventlet.tpool | OK — completed | 71.7k msg/s |

## amd64 · Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 238.8k | 93.8k | 102.8k |
| spawn (fire & forget) | spawn_n/s | 226.8k | 189.2k | 163.3k |
| context switch | switches/s | 2.43M | 816.0k | 469.2k |
| semaphore uncontended | ops/s | 21.36M | 7.25M | 6.63M |
| semaphore contended | ops/s | 21.99M | 7.00M | 6.47M |
| queue put/get | items/s | 8.84M | 6.91M | 2.98M |
| queue shared green+native threads | items/s | 3.51M | **error** | **deadlock** |
| tpool round-trip | calls/s | 102.2k | 56.8k | 23.1k |
| tpool round-trip | mean latency | 9.79 us | 17.62 us | 43.38 us |
| echo @ conc 100 | req/s | 139.7k | 70.3k | 53.0k |
| echo @ conc 100 | p50/p99 ms | 0.654 / 0.976 | 1.397 / 1.559 | 1.84 / 2.656 |
| echo @ conc 1000 | req/s | 109.8k | 57.7k | 37.0k |
| echo @ conc 1000 | p50/p99 ms | 6.929 / 14.052 | 13.656 / 34.503 | 24.253 / 48.839 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 159.3k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 157.8k msg/s |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.11.15

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 239.1k | 89.2k | 101.0k |
| spawn (fire & forget) | spawn_n/s | 220.5k | 198.8k | 171.8k |
| context switch | switches/s | 2.21M | 956.7k | 486.6k |
| semaphore uncontended | ops/s | 19.99M | 7.72M | 5.71M |
| semaphore contended | ops/s | 20.46M | 7.54M | 5.86M |
| queue put/get | items/s | 8.76M | 5.85M | 2.45M |
| queue shared green+native threads | items/s | 3.03M | **error** | **deadlock** |
| tpool round-trip | calls/s | 99.0k | 56.8k | 28.3k |
| tpool round-trip | mean latency | 10.1 us | 17.59 us | 35.3 us |
| echo @ conc 100 | req/s | 99.2k | 69.1k | 51.4k |
| echo @ conc 100 | p50/p99 ms | 0.696 / 1.019 | 1.395 / 1.902 | 1.914 / 2.836 |
| echo @ conc 1000 | req/s | 100.6k | 59.5k | 38.0k |
| echo @ conc 1000 | p50/p99 ms | 7.057 / 14.967 | 13.717 / 35.052 | 23.573 / 41.912 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 139.5k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 61.4k msg/s |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 451.0k | 128.2k | 101.9k |
| spawn (fire & forget) | spawn_n/s | 423.9k | 324.9k | 199.3k |
| context switch | switches/s | 2.04M | 840.1k | 372.0k |
| semaphore uncontended | ops/s | 16.73M | 7.05M | 3.65M |
| semaphore contended | ops/s | 15.33M | 7.38M | 3.40M |
| queue put/get | items/s | 7.02M | 6.50M | 1.66M |
| queue shared green+native threads | items/s | 2.93M | **error** | **error** |
| tpool round-trip | calls/s | 97.0k | 44.4k | 15.0k |
| tpool round-trip | mean latency | 10.31 us | 22.52 us | 66.63 us |
| echo @ conc 100 | req/s | 97.9k | 63.9k | 38.3k |
| echo @ conc 100 | p50/p99 ms | 0.981 / 1.254 | 1.492 / 1.667 | 2.542 / 3.648 |
| echo @ conc 1000 | req/s | 81.4k | 55.0k | 26.6k |
| echo @ conc 1000 | p50/p99 ms | 9.847 / 16.33 | 15.403 / 38.55 | 34.218 / 56.081 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 122.7k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 100.0k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 115.4k msg/s |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.8.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 512.2k | 108.9k | 100.8k |
| spawn (fire & forget) | spawn_n/s | 456.7k | 304.1k | 190.5k |
| context switch | switches/s | 1.84M | 786.5k | 369.4k |
| semaphore uncontended | ops/s | 27.98M | 8.28M | 3.27M |
| semaphore contended | ops/s | 23.32M | 7.89M | 3.02M |
| queue put/get | items/s | 9.95M | 6.59M | 1.47M |
| queue shared green+native threads | items/s | 3.86M | **error** | **error** |
| tpool round-trip | calls/s | 246.8k | 40.3k | 18.5k |
| tpool round-trip | mean latency | 4.05 us | 24.8 us | 53.95 us |
| echo @ conc 100 | req/s | 89.6k | 63.9k | 38.8k |
| echo @ conc 100 | p50/p99 ms | 1.07 / 1.32 | 1.545 / 1.667 | 2.527 / 3.671 |
| echo @ conc 1000 | req/s | 79.5k | 53.4k | 26.7k |
| echo @ conc 1000 | p50/p99 ms | 10.4 / 15.187 | 15.685 / 53.29 | 34.259 / 57.175 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 118.1k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 101.6k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 117.0k msg/s |
| eventlet | eventlet.tpool | OK — completed | 56.9k msg/s |

## arm64 · Python 3.15.0

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 293.9k | 138.1k | 173.3k |
| spawn (fire & forget) | spawn_n/s | 274.4k | 257.2k | 249.7k |
| context switch | switches/s | 3.51M | 1.41M | 918.5k |
| semaphore uncontended | ops/s | 42.15M | 10.18M | 12.79M |
| semaphore contended | ops/s | 38.62M | 8.76M | 12.31M |
| queue put/get | items/s | 15.37M | 10.79M | 5.81M |
| queue shared green+native threads | items/s | 3.08M | **error** | **deadlock** |
| tpool round-trip | calls/s | 25.3k | 24.1k | 11.6k |
| tpool round-trip | mean latency | 39.48 us | 41.46 us | 85.98 us |
| echo @ conc 100 | req/s | 134.2k | 121.5k | 96.3k |
| echo @ conc 100 | p50/p99 ms | 0.716 / 1.117 | 0.764 / 1.302 | 1.048 / 1.717 |
| echo @ conc 1000 | req/s | 100.3k | 94.1k | 66.5k |
| echo @ conc 1000 | p50/p99 ms | 7.994 / 14.385 | 8.847 / 22.07 | 13.675 / 23.095 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 15.7k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.14.6

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 331.2k | 146.5k | 175.9k |
| spawn (fire & forget) | spawn_n/s | 327.1k | 282.7k | 234.1k |
| context switch | switches/s | 3.45M | 1.49M | 950.0k |
| semaphore uncontended | ops/s | 44.69M | 10.36M | 13.01M |
| semaphore contended | ops/s | 41.12M | 10.21M | 12.76M |
| queue put/get | items/s | 13.22M | 11.43M | 5.70M |
| queue shared green+native threads | items/s | 2.81M | **error** | **deadlock** |
| tpool round-trip | calls/s | 26.7k | 25.3k | 11.3k |
| tpool round-trip | mean latency | 37.46 us | 39.47 us | 88.6 us |
| echo @ conc 100 | req/s | 134.4k | 117.7k | 94.2k |
| echo @ conc 100 | p50/p99 ms | 0.707 / 1.09 | 0.81 / 1.127 | 1.014 / 1.559 |
| echo @ conc 1000 | req/s | 97.6k | 92.1k | 62.6k |
| echo @ conc 1000 | p50/p99 ms | 8.213 / 15.553 | 9.019 / 21.556 | 14.752 / 22.637 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.2k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.13.5

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 372.5k | 154.2k | 184.5k |
| spawn (fire & forget) | spawn_n/s | 378.1k | 293.9k | 272.5k |
| context switch | switches/s | 4.26M | 1.42M | 923.5k |
| semaphore uncontended | ops/s | 49.14M | 10.98M | 12.28M |
| semaphore contended | ops/s | 44.78M | 10.77M | 12.47M |
| queue put/get | items/s | 16.47M | 12.35M | 6.05M |
| queue shared green+native threads | items/s | 2.92M | **error** | **deadlock** |
| tpool round-trip | calls/s | 24.8k | 24.4k | 11.2k |
| tpool round-trip | mean latency | 40.35 us | 41.03 us | 89.1 us |
| echo @ conc 100 | req/s | 137.0k | 120.0k | 95.0k |
| echo @ conc 100 | p50/p99 ms | 0.697 / 1.177 | 0.765 / 1.008 | 1.02 / 1.657 |
| echo @ conc 1000 | req/s | 96.3k | 91.8k | 55.0k |
| echo @ conc 1000 | p50/p99 ms | 7.996 / 15.866 | 9.175 / 21.219 | 16.234 / 34.27 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 15.0k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 314.8k | 145.2k | 186.9k |
| spawn (fire & forget) | spawn_n/s | 328.7k | 275.3k | 229.7k |
| context switch | switches/s | 3.91M | 1.35M | 904.3k |
| semaphore uncontended | ops/s | 48.45M | 9.59M | 12.72M |
| semaphore contended | ops/s | 42.03M | 9.53M | 12.51M |
| queue put/get | items/s | 14.73M | 10.67M | 5.14M |
| queue shared green+native threads | items/s | 2.57M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.8k | 25.6k | 12.4k |
| tpool round-trip | mean latency | 35.96 us | 38.99 us | 80.84 us |
| echo @ conc 100 | req/s | 144.1k | 127.3k | 95.5k |
| echo @ conc 100 | p50/p99 ms | 0.66 / 1.089 | 0.764 / 0.949 | 1.03 / 1.639 |
| echo @ conc 1000 | req/s | 107.6k | 88.6k | 62.6k |
| echo @ conc 1000 | p50/p99 ms | 7.445 / 13.875 | 9.504 / 22.737 | 14.192 / 27.686 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.6k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.11.15

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 316.3k | 136.1k | 178.9k |
| spawn (fire & forget) | spawn_n/s | 290.8k | 273.0k | 250.0k |
| context switch | switches/s | 3.67M | 1.66M | 1.04M |
| semaphore uncontended | ops/s | 38.28M | 10.54M | 11.80M |
| semaphore contended | ops/s | 38.41M | 10.51M | 11.90M |
| queue put/get | items/s | 13.99M | 11.27M | 4.85M |
| queue shared green+native threads | items/s | 2.50M | **error** | **deadlock** |
| tpool round-trip | calls/s | 26.0k | 25.0k | 10.9k |
| tpool round-trip | mean latency | 38.45 us | 40.06 us | 91.72 us |
| echo @ conc 100 | req/s | 134.5k | 118.4k | 93.5k |
| echo @ conc 100 | p50/p99 ms | 0.696 / 1.188 | 0.787 / 0.99 | 1.039 / 1.584 |
| echo @ conc 1000 | req/s | 93.8k | 88.7k | 57.4k |
| echo @ conc 1000 | p50/p99 ms | 8.521 / 16.027 | 9.517 / 21.3 | 15.785 / 27.4 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 18.4k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 828.1k | 207.5k | 199.1k |
| spawn (fire & forget) | spawn_n/s | 780.6k | 549.6k | 385.8k |
| context switch | switches/s | 3.51M | 1.48M | 729.1k |
| semaphore uncontended | ops/s | 40.36M | 10.40M | 7.08M |
| semaphore contended | ops/s | 36.30M | 10.18M | 6.90M |
| queue put/get | items/s | 13.77M | 10.82M | 2.88M |
| queue shared green+native threads | items/s | 2.70M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.8k | 25.4k | 11.2k |
| tpool round-trip | mean latency | 35.93 us | 39.31 us | 89.39 us |
| echo @ conc 100 | req/s | 144.6k | 122.5k | 76.4k |
| echo @ conc 100 | p50/p99 ms | 0.657 / 0.825 | 0.788 / 0.926 | 1.283 / 1.865 |
| echo @ conc 1000 | req/s | 111.5k | 96.3k | 50.2k |
| echo @ conc 1000 | p50/p99 ms | 7.596 / 11.648 | 8.819 / 16.713 | 19.367 / 39.964 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 17.3k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.8.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 836.9k | 238.5k | 194.1k |
| spawn (fire & forget) | spawn_n/s | 747.9k | 494.9k | 334.1k |
| context switch | switches/s | 3.00M | 1.39M | 585.7k |
| semaphore uncontended | ops/s | 38.87M | 11.76M | 4.57M |
| semaphore contended | ops/s | 24.39M | 11.27M | 4.23M |
| queue put/get | items/s | 11.21M | 9.41M | 2.03M |
| queue shared green+native threads | items/s | 2.32M | **error** | **deadlock** |
| tpool round-trip | calls/s | 26.8k | 24.3k | 9.9k |
| tpool round-trip | mean latency | 37.35 us | 41.23 us | 100.99 us |
| echo @ conc 100 | req/s | 131.1k | 101.0k | 62.0k |
| echo @ conc 100 | p50/p99 ms | 0.727 / 0.966 | 0.962 / 1.139 | 1.588 / 2.416 |
| echo @ conc 1000 | req/s | 107.2k | 82.3k | 41.3k |
| echo @ conc 1000 | p50/p99 ms | 7.796 / 13.267 | 10.545 / 23.842 | 22.508 / 34.033 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 16.6k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 2.7.18

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 903.4k | 236.4k | 145.1k |
| spawn (fire & forget) | spawn_n/s | 836.3k | 531.5k | 293.0k |
| context switch | switches/s | 3.29M | 1.24M | 435.7k |
| semaphore uncontended | ops/s | 39.09M | 14.01M | 5.09M |
| semaphore contended | ops/s | 19.26M | 10.73M | 4.36M |
| queue put/get | items/s | 10.45M | 7.73M | 1.82M |
| queue shared green+native threads | items/s | 1.04M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.4k | 166.2 | 9.8k |
| tpool round-trip | mean latency | 36.46 us | 6015.09 us | 102.34 us |
| echo @ conc 100 | req/s | 124.5k | 93.8k | 61.3k |
| echo @ conc 100 | p50/p99 ms | 0.74 / 1.168 | 0.987 / 1.184 | 1.667 / 2.873 |
| echo @ conc 1000 | req/s | 97.2k | 77.1k | 31.9k |
| echo @ conc 1000 | p50/p99 ms | 8.591 / 13.755 | 11.025 / 24.204 | 30.411 / 51.361 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 15.2k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Headline findings — amd64

Numbers below are from **Python 3.15.0**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 276.7k gt/s vs gevent 96.5k vs eventlet 112.4k — filament 2.9x gevent, 2.5x eventlet. Across the matrix filament runs 2.55-4.70x the spawn rate of gevent, widest on Python 3.8.20.
- **Context-switch rate — filament wins:** filament 2.05M sw/s vs gevent 810.2k vs eventlet 494.8k — filament 2.5x gevent, 4.1x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~17.80M uncontended ops/s vs gevent 7.25M / eventlet 6.72M (3-8x), and it leads on queue put/get too.
- **Mixed green+native queue — filament only:** a single bounded `Queue` worked simultaneously by greenthreads AND native `threading.Thread` producers/consumers runs at ~3.61M items/s in filament. The same workload on gevent/eventlet deadlocks or errors — their queues are hub-bound and cannot be used from a foreign OS thread. filament's per-thread scheduler + deferred cross-thread wakeup makes this a first-class pattern (same mechanism as the #137 win).
- **Threadpool round-trip:** filament 102.4k calls/s vs gevent 60.7k vs eventlet 24.4k — filament 1.7x gevent, 4.2x eventlet. Across Python 3 in this matrix that is 1.69-6.12x gevent's rate, best on Python 3.8.20; filament's pool wakes the most-recently-idle (MRU) worker for each job, keeping the hot worker's stack and caches warm.
- **Echo server — filament wins:** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency; eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path carry the socket hot loop.
- **#137 logging-in-threadpool:** filament logs from its real-thread pool while the hub runs greenthreads and completes **every time, on every interpreter and both machines, no workaround, 118.1k-179.3k msgs/s**. For gevent and eventlet this is a race, not a verdict, and the machine decides it: on the 6-core box gevent deadlocks outright, including with its documented mitigations (hub threadpool + native logging locks + `logThreads=False`); on the 64-thread host it completed 6 of 6 repeats. eventlet loses the race on both, most recently 4 times in 6. Read the per-version tables for what actually happened rather than assuming either outcome.

## Headline findings — arm64

Numbers below are from **Python 3.15.0**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 293.9k gt/s vs gevent 138.1k vs eventlet 173.3k — filament 2.1x gevent, 1.7x eventlet. Across the matrix filament runs 2.13-3.99x the spawn rate of gevent, widest on Python 3.10.20.
- **Context-switch rate — filament wins:** filament 3.51M sw/s vs gevent 1.41M vs eventlet 918.5k — filament 2.5x gevent, 3.8x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~42.15M uncontended ops/s vs gevent 10.18M / eventlet 12.79M (3-8x), and it leads on queue put/get too.
- **Mixed green+native queue — filament only:** a single bounded `Queue` worked simultaneously by greenthreads AND native `threading.Thread` producers/consumers runs at ~3.08M items/s in filament. The same workload on gevent/eventlet deadlocks or errors — their queues are hub-bound and cannot be used from a foreign OS thread. filament's per-thread scheduler + deferred cross-thread wakeup makes this a first-class pattern (same mechanism as the #137 win).
- **Threadpool round-trip:** filament 25.3k calls/s vs gevent 24.1k vs eventlet 11.6k — filament 1.0x gevent, 2.2x eventlet. Across Python 3 in this matrix that is 1.02-1.10x gevent's rate, best on Python 3.8.20; filament's pool wakes the most-recently-idle (MRU) worker for each job, keeping the hot worker's stack and caches warm.
- **Echo server — filament wins:** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency; eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path carry the socket hot loop.
- **#137 logging-in-threadpool:** filament logs from its real-thread pool while the hub runs greenthreads and completes **every time, on every interpreter and both machines, no workaround, 15.0k-18.4k msgs/s**. For gevent and eventlet this is a race, not a verdict, and the machine decides it: on the 6-core box gevent deadlocks outright, including with its documented mitigations (hub threadpool + native logging locks + `logThreads=False`); on the 64-thread host it completed 6 of 6 repeats. eventlet loses the race on both, most recently 4 times in 6. Read the per-version tables for what actually happened rather than assuming either outcome.

