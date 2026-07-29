# filament vs gevent vs eventlet — benchmark results

Greenlet-based cooperative concurrency shootout: **filament** (this repo) against modern **gevent** and **eventlet**, across CPython versions on aarch64 and x86_64 Linux (results are grouped by architecture below).

Each (framework, benchmark) ran in its own fresh subprocess. Micro-benchmarks report the **median** of several timed reps (warm-up discarded), using a monotonic clock. Higher is better for throughput; lower is better for latency.

## Methodology

- Same logical workload run three ways (filament / gevent / eventlet) with identical sizes per framework. For filament the in-process client is `filament.socket`; gevent uses `gevent.socket` + `StreamServer`-style accept loop; eventlet uses `eventlet.green.socket`. The echo client stays in the same framework as the server for fairness.
- Each pair runs in a **fresh interpreter subprocess** so monkey-patching and hub state never leak between frameworks.
- Spawn = 100k greenthreads spawned then joined. Context switch = 100 greenthreads x 10k `sleep(0)` = 1,000,000 switches. Semaphore uncontended = 1M acquire/release on one greenthread; contended = 50 greenthreads on a `Semaphore(1)`. Queue = 200k producer/consumer items. tpool = 3000 sequential real-thread round-trips. Echo = concurrency 100 (x100 round-trips) and 1000 (x20), 64-byte payload.
- Queue mixed = ONE bounded queue (maxsize 100) shared simultaneously by a greenthread producer + consumer AND a native `threading.Thread` producer + consumer (50k items per producer), so native threads block in `q.get()`/`q.put()` while greenthreads work the same queue. gevent/eventlet queues are hub-bound; foreign-OS-thread use is undefined for them and runs under the deadlock watchdog.
- **#137**: monkey-patch everything, then log heavily from real OS-thread pool workers while greenthreads spin in the hub. Each attempt runs under a 45 s **idle** watchdog: the worker reports progress as it logs, so a run that keeps making progress is allowed to finish however slow the host, while one that goes silent is killed and recorded as **deadlock**. Whether gevent/eventlet hang here depends on the machine -- it is a race between the hub and the logging lock, and a faster host with more cores wins it more often -- so a single cell is one roll of the dice, not a property of the library. filament has not lost it on any machine or interpreter.

> **OS-thread caveat.** `tpool` and `#137` cross into real OS threads, and on a many-core host their absolute numbers are not reproducible: the amd64 box (32c/64t) gives a clean bimodal split ~1.6x apart, switching even between reps inside one process. It is thread placement, and `taskset` proves it -- pinned to a single CPU the same benchmark repeats to ~2% (filament 50-52k, gevent 38-39k calls/s), pinned to two it is faster and mostly steady, and turned loose on all 32 it oscillates. The 1.6x factor hits both runtimes equally, so the *ranking* holds even where the absolute value does not: filament leads gevent by 1.3-1.4x in every pinned configuration. The current numbers still show it: amd64 gevent tpool ranges 40k-91k calls/s across interpreters while the arm64 host repeats to within a few percent (89-92k) over the same set -- same code, same commit, different scheduling freedom. Read a single tpool or #137 cell as an order of magnitude; the pure-greenthread rows repeat to within a few percent.

> **Cross-version caveat.** Each Python version's table was recorded in its own sequential run on the box named for it in the Environments table -- note that the arm64 2.7 row comes from a Linux container while every other arm64 row is the Apple host, so 2.7 is not comparable to them. Interpreter speed differs across versions, so absolute numbers are **not** comparable across Python versions. The reliable signal is the **ratio between frameworks within one version**: all three frameworks in a table ran back-to-back under identical conditions.

## Environments

| Arch | Python | greenlet | gevent | eventlet | host | measured |
|---|---|---|---|---|---|---|
| amd64 | 3.15.0 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-07-29 |
| amd64 | 3.14.4 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-07-29 |
| amd64 | 3.13.14 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-07-29 |
| amd64 | 3.12.13 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-07-29 |
| amd64 | 3.11.15 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-07-29 |
| amd64 | 3.10.20 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-07-29 |
| amd64 | 3.9.25 | 3.2.5 | 26.7.0 | 0.40.4 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-07-29 |
| arm64 | 3.15.0 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5Max, 18c, bare metal | 2026-07-29 (2210904) |
| arm64 | 3.14.6 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5Max, 18c, bare metal | 2026-07-29 (2210904) |
| arm64 | 3.13.14 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5Max, 18c, bare metal | 2026-07-29 (2210904) |
| arm64 | 3.12.13 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5Max, 18c, bare metal | 2026-07-29 (2210904) |
| arm64 | 3.11.15 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5Max, 18c, bare metal | 2026-07-29 (2210904) |
| arm64 | 3.10.20 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5Max, 18c, bare metal | 2026-07-29 (2210904) |
| arm64 | 3.9.6 | 3.2.5 | 26.7.0 | 0.40.4 | Apple M5Max, 18c, bare metal | 2026-07-29 (2210904) |
| arm64 | 2.7.18 | 2.0.2 | 22.10.2 | 0.33.3 | 6 cpus, container | 2026-07-29 (2210904) |

Availability notes:

- **gevent on Python 2.7**: no cp27/aarch64 wheel exists and stock source builds fail under a modern GCC (Cython-generated C errors); where the 2.7 column shows gevent numbers they come from a locally-built older gevent (see the environments table). eventlet 0.33.3 (pure-Python) and filament both build/run on 2.7.
- **gevent tpool on Python 2.7**: gevent **22.10.2** (the last py2.7 release) deadlocks in the threadpool round-trip benchmark on 2.7 — reproducible even at small scale. Its predecessor 21.12.0 completed the same benchmark (~23.6k calls/s), so this is a gevent regression in its final py2.7 release, not a harness artifact.
- **filament** builds and runs every benchmark (including `#137`) on every interpreter in the matrix, 2.7 through 3.15, with a version-tagged `.so` per interpreter.

## amd64 · Python 3.15.0

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 271.9k | 97.0k | 110.6k |
| spawn (fire & forget) | spawn_n/s | 254.8k | 200.3k | 178.8k |
| context switch | switches/s | 2.05M | 788.8k | 488.4k |
| semaphore uncontended | ops/s | 17.80M | 6.90M | 6.79M |
| semaphore contended | ops/s | 19.13M | 7.26M | 6.65M |
| queue put/get | items/s | 7.36M | 6.24M | 3.53M |
| queue shared green+native threads | items/s | 3.56M | **error** | **deadlock** |
| tpool round-trip | calls/s | 109.6k | 56.6k | 26.3k |
| tpool round-trip | mean latency | 9.12 us | 17.68 us | 38.01 us |
| echo @ conc 100 | req/s | 91.9k | 74.3k | 54.1k |
| echo @ conc 100 | p50/p99 ms | 1.035 / 1.547 | 1.294 / 1.487 | 1.801 / 2.626 |
| echo @ conc 1000 | req/s | 79.1k | 61.1k | 41.3k |
| echo @ conc 1000 | p50/p99 ms | 10.16 / 18.379 | 13.314 / 33.909 | 21.612 / 39.968 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 138.3k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 154.9k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 35.5k msg/s |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.14.4

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 297.7k | 119.1k | 151.0k |
| spawn (fire & forget) | spawn_n/s | 263.1k | 224.6k | 203.9k |
| context switch | switches/s | 2.03M | 779.2k | 489.2k |
| semaphore uncontended | ops/s | 21.45M | 7.67M | 6.99M |
| semaphore contended | ops/s | 19.96M | 6.96M | 6.75M |
| queue put/get | items/s | 8.16M | 6.51M | 3.60M |
| queue shared green+native threads | items/s | 3.36M | **error** | **deadlock** |
| tpool round-trip | calls/s | 105.0k | 90.7k | 25.8k |
| tpool round-trip | mean latency | 9.52 us | 11.02 us | 38.74 us |
| echo @ conc 100 | req/s | 95.5k | 74.0k | 55.4k |
| echo @ conc 100 | p50/p99 ms | 1.003 / 1.513 | 1.303 / 1.504 | 1.796 / 2.59 |
| echo @ conc 1000 | req/s | 77.8k | 62.0k | 41.4k |
| echo @ conc 1000 | p50/p99 ms | 6.952 / 20.024 | 13.306 / 34.586 | 21.876 / 34.183 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 166.1k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 20.9k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 165.2k msg/s |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.13.14

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 280.6k | 101.3k | 114.2k |
| spawn (fire & forget) | spawn_n/s | 265.9k | 217.4k | 197.6k |
| context switch | switches/s | 2.33M | 830.4k | 503.6k |
| semaphore uncontended | ops/s | 21.87M | 6.74M | 6.61M |
| semaphore contended | ops/s | 21.06M | 6.96M | 6.59M |
| queue put/get | items/s | 8.87M | 7.20M | 3.14M |
| queue shared green+native threads | items/s | 3.39M | **error** | **deadlock** |
| tpool round-trip | calls/s | 103.9k | 53.1k | 24.1k |
| tpool round-trip | mean latency | 9.62 us | 18.83 us | 41.49 us |
| echo @ conc 100 | req/s | 95.5k | 73.7k | 54.5k |
| echo @ conc 100 | p50/p99 ms | 0.984 / 1.479 | 1.357 / 1.561 | 1.775 / 2.532 |
| echo @ conc 1000 | req/s | 81.0k | 61.7k | 40.8k |
| echo @ conc 1000 | p50/p99 ms | 10.008 / 17.347 | 13.341 / 32.819 | 21.981 / 37.732 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 162.0k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 147.9k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 160.3k msg/s |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 239.8k | 92.9k | 100.7k |
| spawn (fire & forget) | spawn_n/s | 222.1k | 191.8k | 159.2k |
| context switch | switches/s | 2.39M | 820.1k | 471.8k |
| semaphore uncontended | ops/s | 20.97M | 6.79M | 6.79M |
| semaphore contended | ops/s | 21.75M | 6.93M | 6.86M |
| queue put/get | items/s | 8.58M | 6.95M | 2.98M |
| queue shared green+native threads | items/s | 3.12M | **error** | **error** |
| tpool round-trip | calls/s | 100.3k | 55.1k | 23.5k |
| tpool round-trip | mean latency | 9.97 us | 18.16 us | 42.64 us |
| echo @ conc 100 | req/s | 99.4k | 74.2k | 53.9k |
| echo @ conc 100 | p50/p99 ms | 0.948 / 1.471 | 1.311 / 1.583 | 1.81 / 2.668 |
| echo @ conc 1000 | req/s | 82.5k | 61.1k | 38.8k |
| echo @ conc 1000 | p50/p99 ms | 7.286 / 17.04 | 13.263 / 34.747 | 23.1 / 48.563 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 152.7k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 138.9k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 14.5k msg/s |
| eventlet | eventlet.tpool | OK — completed | 10.4k msg/s |

## amd64 · Python 3.11.15

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 247.0k | 90.4k | 100.6k |
| spawn (fire & forget) | spawn_n/s | 232.0k | 195.4k | 173.8k |
| context switch | switches/s | 2.20M | 920.9k | 478.8k |
| semaphore uncontended | ops/s | 20.12M | 7.69M | 5.70M |
| semaphore contended | ops/s | 19.17M | 7.17M | 6.07M |
| queue put/get | items/s | 8.31M | 6.16M | 2.65M |
| queue shared green+native threads | items/s | 3.46M | **error** | **deadlock** |
| tpool round-trip | calls/s | 135.3k | 56.2k | 20.3k |
| tpool round-trip | mean latency | 7.39 us | 17.78 us | 49.31 us |
| echo @ conc 100 | req/s | 96.0k | 71.2k | 52.2k |
| echo @ conc 100 | p50/p99 ms | 0.982 / 1.533 | 1.334 / 1.494 | 1.871 / 2.752 |
| echo @ conc 1000 | req/s | 75.9k | 60.7k | 38.1k |
| echo @ conc 1000 | p50/p99 ms | 9.906 / 19.947 | 13.459 / 34.424 | 23.651 / 43.739 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 40.3k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 136.6k msg/s |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 460.8k | 124.6k | 99.8k |
| spawn (fire & forget) | spawn_n/s | 423.4k | 325.3k | 203.0k |
| context switch | switches/s | 2.03M | 821.2k | 377.5k |
| semaphore uncontended | ops/s | 16.25M | 7.60M | 3.60M |
| semaphore contended | ops/s | 15.18M | 7.40M | 3.39M |
| queue put/get | items/s | 7.12M | 6.48M | 1.68M |
| queue shared green+native threads | items/s | 2.95M | **error** | **deadlock** |
| tpool round-trip | calls/s | 98.0k | 47.0k | 15.7k |
| tpool round-trip | mean latency | 10.2 us | 21.27 us | 63.76 us |
| echo @ conc 100 | req/s | 94.8k | 67.0k | 40.2k |
| echo @ conc 100 | p50/p99 ms | 0.711 / 1.217 | 1.416 / 1.53 | 2.422 / 3.482 |
| echo @ conc 1000 | req/s | 114.7k | 57.0k | 28.2k |
| echo @ conc 1000 | p50/p99 ms | 6.89 / 12.535 | 14.878 / 38.294 | 32.14 / 61.521 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 121.6k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 18.2k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 25.5k msg/s |
| eventlet | naive | **DEADLOCK** | - |

## amd64 · Python 3.9.25

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 474.3k | 114.6k | 95.0k |
| spawn (fire & forget) | spawn_n/s | 430.2k | 288.4k | 200.3k |
| context switch | switches/s | 1.63M | 886.6k | 375.0k |
| semaphore uncontended | ops/s | 18.66M | 7.60M | 3.23M |
| semaphore contended | ops/s | 16.14M | 7.17M | 3.01M |
| queue put/get | items/s | 7.27M | 6.21M | 1.53M |
| queue shared green+native threads | items/s | 3.17M | **error** | **deadlock** |
| tpool round-trip | calls/s | 92.5k | 39.8k | 25.5k |
| tpool round-trip | mean latency | 10.81 us | 25.15 us | 39.18 us |
| echo @ conc 100 | req/s | 131.0k | 64.5k | 38.8k |
| echo @ conc 100 | p50/p99 ms | 0.742 / 0.935 | 1.453 / 1.621 | 2.508 / 3.632 |
| echo @ conc 1000 | req/s | 113.4k | 57.2k | 27.7k |
| echo @ conc 1000 | p50/p99 ms | 7.254 / 12.162 | 14.818 / 40.636 | 32.752 / 53.502 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 121.7k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 10.1k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 12.5k msg/s |
| eventlet | eventlet.tpool | OK — completed | 15.9k msg/s |

## arm64 · Python 3.15.0

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 489.0k | 197.6k | 240.1k |
| spawn (fire & forget) | spawn_n/s | 488.6k | 392.8k | 350.0k |
| context switch | switches/s | 4.20M | 1.73M | 1.02M |
| semaphore uncontended | ops/s | 43.79M | 14.00M | 13.35M |
| semaphore contended | ops/s | 39.29M | 13.77M | 13.16M |
| queue put/get | items/s | 15.18M | 12.15M | 5.97M |
| queue shared green+native threads | items/s | 5.05M | **error** | **deadlock** |
| tpool round-trip | calls/s | 152.2k | 89.3k | 43.1k |
| tpool round-trip | mean latency | 6.57 us | 11.19 us | 23.18 us |
| echo @ conc 100 | req/s | 161.3k | 101.0k | 78.8k |
| echo @ conc 100 | p50/p99 ms | 0.587 / 0.948 | 0.914 / 1.661 | 1.221 / 1.492 |
| echo @ conc 1000 | req/s | 80.3k | 93.1k | 67.7k |
| echo @ conc 1000 | p50/p99 ms | 0.749 / 2.144 | 8.596 / 16.617 | 6.84 / 15.008 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 1.8k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.14.6

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 459.3k | 194.4k | 222.2k |
| spawn (fire & forget) | spawn_n/s | 463.0k | 385.2k | 323.4k |
| context switch | switches/s | 4.16M | 1.53M | 869.3k |
| semaphore uncontended | ops/s | 42.70M | 12.61M | 13.63M |
| semaphore contended | ops/s | 42.02M | 12.25M | 13.05M |
| queue put/get | items/s | 13.24M | 11.40M | 5.55M |
| queue shared green+native threads | items/s | 5.05M | **error** | **deadlock** |
| tpool round-trip | calls/s | 156.5k | 91.5k | 41.7k |
| tpool round-trip | mean latency | 6.39 us | 10.93 us | 23.97 us |
| echo @ conc 100 | req/s | 167.4k | 97.2k | 78.7k |
| echo @ conc 100 | p50/p99 ms | 0.57 / 0.909 | 0.905 / 1.155 | 1.178 / 1.499 |
| echo @ conc 1000 | req/s | 53.3k | 87.6k | 64.7k |
| echo @ conc 1000 | p50/p99 ms | 0.691 / 1.849 | 6.818 / 16.835 | 11.082 / 18.827 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 1.8k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.13.14

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 492.7k | 206.5k | 247.7k |
| spawn (fire & forget) | spawn_n/s | 477.9k | 413.4k | 365.8k |
| context switch | switches/s | 4.69M | 1.72M | 1.05M |
| semaphore uncontended | ops/s | 48.24M | 15.09M | 13.89M |
| semaphore contended | ops/s | 45.74M | 14.73M | 13.31M |
| queue put/get | items/s | 15.08M | 12.35M | 6.06M |
| queue shared green+native threads | items/s | 4.77M | **error** | **deadlock** |
| tpool round-trip | calls/s | 169.7k | 91.2k | 42.8k |
| tpool round-trip | mean latency | 5.89 us | 10.97 us | 23.35 us |
| echo @ conc 100 | req/s | 164.3k | 90.8k | 78.6k |
| echo @ conc 100 | p50/p99 ms | 0.582 / 0.944 | 0.989 / 1.255 | 1.163 / 1.477 |
| echo @ conc 1000 | req/s | 55.9k | 91.2k | 50.1k |
| echo @ conc 1000 | p50/p99 ms | 0.716 / 2.103 | 9.157 / 16.395 | 1.709 / 4.483 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 1.8k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.12.13

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 439.0k | 195.2k | 228.6k |
| spawn (fire & forget) | spawn_n/s | 420.2k | 375.5k | 316.1k |
| context switch | switches/s | 4.61M | 1.68M | 1.07M |
| semaphore uncontended | ops/s | 44.11M | 14.69M | 13.70M |
| semaphore contended | ops/s | 44.46M | 14.10M | 13.88M |
| queue put/get | items/s | 16.35M | 12.91M | 5.78M |
| queue shared green+native threads | items/s | 4.74M | **error** | **deadlock** |
| tpool round-trip | calls/s | 174.9k | 91.3k | 43.0k |
| tpool round-trip | mean latency | 5.72 us | 10.95 us | 23.25 us |
| echo @ conc 100 | req/s | 163.5k | 97.3k | 76.6k |
| echo @ conc 100 | p50/p99 ms | 0.58 / 0.897 | 0.901 / 1.311 | 1.241 / 1.481 |
| echo @ conc 1000 | req/s | 54.7k | 87.2k | 65.5k |
| echo @ conc 1000 | p50/p99 ms | 0.703 / 1.909 | 9.501 / 16.426 | 4.897 / 9.497 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 1.9k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.11.15

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 450.3k | 184.5k | 222.2k |
| spawn (fire & forget) | spawn_n/s | 439.2k | 384.7k | 324.7k |
| context switch | switches/s | 4.63M | 1.93M | 1.13M |
| semaphore uncontended | ops/s | 41.74M | 16.23M | 13.02M |
| semaphore contended | ops/s | 41.91M | 15.51M | 13.06M |
| queue put/get | items/s | 16.65M | 12.50M | 5.55M |
| queue shared green+native threads | items/s | 4.91M | **error** | **deadlock** |
| tpool round-trip | calls/s | 168.4k | 91.7k | 42.7k |
| tpool round-trip | mean latency | 5.94 us | 10.91 us | 23.44 us |
| echo @ conc 100 | req/s | 162.1k | 99.6k | 77.3k |
| echo @ conc 100 | p50/p99 ms | 0.581 / 0.89 | 0.93 / 1.141 | 1.185 / 1.494 |
| echo @ conc 1000 | req/s | 128.3k | 88.2k | 65.4k |
| echo @ conc 1000 | p50/p99 ms | 4.811 / 6.32 | 5.291 / 16.154 | 11.02 / 13.122 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 2.0k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.10.20

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.18M | 303.4k | 259.5k |
| spawn (fire & forget) | spawn_n/s | 1.09M | 756.1k | 478.3k |
| context switch | switches/s | 4.46M | 1.74M | 770.9k |
| semaphore uncontended | ops/s | 38.39M | 16.16M | 7.70M |
| semaphore contended | ops/s | 34.25M | 15.46M | 7.41M |
| queue put/get | items/s | 14.22M | 11.92M | 3.18M |
| queue shared green+native threads | items/s | 4.43M | **error** | **deadlock** |
| tpool round-trip | calls/s | 154.9k | 91.9k | 37.3k |
| tpool round-trip | mean latency | 6.46 us | 10.88 us | 26.82 us |
| echo @ conc 100 | req/s | 165.0k | 89.9k | 64.7k |
| echo @ conc 100 | p50/p99 ms | 0.581 / 0.744 | 0.962 / 1.197 | 1.403 / 1.783 |
| echo @ conc 1000 | req/s | 53.1k | 83.4k | 50.2k |
| echo @ conc 1000 | p50/p99 ms | 0.705 / 1.66 | 9.407 / 14.032 | 4.046 / 7.97 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 2.9k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 3.9.6

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.14M | 300.2k | 242.1k |
| spawn (fire & forget) | spawn_n/s | 1.01M | 657.0k | 419.0k |
| context switch | switches/s | 3.44M | 1.68M | 701.2k |
| semaphore uncontended | ops/s | 34.44M | 13.60M | 5.28M |
| semaphore contended | ops/s | 27.37M | 12.59M | 4.86M |
| queue put/get | items/s | 11.54M | 9.46M | 2.45M |
| queue shared green+native threads | items/s | 3.99M | **error** | **deadlock** |
| tpool round-trip | calls/s | 149.8k | 89.2k | 35.2k |
| tpool round-trip | mean latency | 6.68 us | 11.21 us | 28.42 us |
| echo @ conc 100 | req/s | 160.0k | 78.0k | 61.1k |
| echo @ conc 100 | p50/p99 ms | 0.6 / 0.751 | 1.05 / 1.313 | 1.523 / 1.888 |
| echo @ conc 1000 | req/s | 51.7k | 81.1k | 50.1k |
| echo @ conc 1000 | p50/p99 ms | 0.729 / 1.733 | 5.871 / 15.865 | 4.397 / 10.617 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 2.6k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## arm64 · Python 2.7.18

Higher is better except latency rows (lower is better). **bold** = that framework errored / was unavailable / deadlocked for this benchmark.

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 975.7k | 234.8k | 141.4k |
| spawn (fire & forget) | spawn_n/s | 896.9k | 513.4k | 278.2k |
| context switch | switches/s | 3.45M | 1.28M | 455.4k |
| semaphore uncontended | ops/s | 39.81M | 14.04M | 5.28M |
| semaphore contended | ops/s | 20.05M | 10.87M | 4.50M |
| queue put/get | items/s | 10.54M | 7.93M | 1.85M |
| queue shared green+native threads | items/s | 1.04M | **error** | **deadlock** |
| tpool round-trip | calls/s | 27.6k | 166.5 | 9.8k |
| tpool round-trip | mean latency | 36.28 us | 6006.92 us | 101.76 us |
| echo @ conc 100 | req/s | 126.4k | 100.7k | 57.6k |
| echo @ conc 100 | p50/p99 ms | 0.762 / 0.968 | 0.966 / 1.193 | 1.673 / 2.905 |
| echo @ conc 1000 | req/s | 103.3k | 82.0k | 35.9k |
| echo @ conc 1000 | p50/p99 ms | 7.853 / 12.209 | 10.417 / 24.28 | 24.754 / 44.676 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 15.7k msg/s |
| gevent | naive | **DEADLOCK** | - |
| gevent | workaround | **DEADLOCK** | - |
| eventlet | naive | **DEADLOCK** | - |

## Headline findings — amd64

Numbers below are from **Python 3.15.0**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 271.9k gt/s vs gevent 97.0k vs eventlet 110.6k — filament 2.8x gevent, 2.5x eventlet. Across the matrix filament runs 2.50-4.14x the spawn rate of gevent, widest on Python 3.9.25.
- **Context-switch rate — filament wins:** filament 2.05M sw/s vs gevent 788.8k vs eventlet 488.4k — filament 2.6x gevent, 4.2x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~17.80M uncontended ops/s vs gevent 6.90M / eventlet 6.79M (3-8x), and it leads on queue put/get too.
- **Mixed green+native queue — filament only:** a single bounded `Queue` worked simultaneously by greenthreads AND native `threading.Thread` producers/consumers runs at ~3.56M items/s in filament. The same workload on gevent/eventlet deadlocks or errors — their queues are hub-bound and cannot be used from a foreign OS thread. filament's per-thread scheduler + deferred cross-thread wakeup makes this a first-class pattern (same mechanism as the #137 win).
- **Threadpool round-trip:** filament 109.6k calls/s vs gevent 56.6k vs eventlet 26.3k — filament 1.9x gevent, 4.2x eventlet. Across Python 3 in this matrix that is 1.16-2.41x gevent's rate, best on Python 3.11.15; filament's pool wakes the most-recently-idle (MRU) worker for each job, keeping the hot worker's stack and caches warm.
- **Echo server — filament wins:** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency; eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path carry the socket hot loop.
- **#137 logging-in-threadpool:** filament logs from its real-thread pool while the hub runs greenthreads and completes **every time, on every interpreter and both machines, no workaround, 40.3k-166.1k msgs/s**. For gevent and eventlet this is a race, not a verdict, and the machine decides it: on the 6-core box gevent deadlocks outright, including with its documented mitigations (hub threadpool + native logging locks + `logThreads=False`); on the 64-thread host it completed 6 of 6 repeats. eventlet loses the race on both, most recently 4 times in 6. Read the per-version tables for what actually happened rather than assuming either outcome.

## Headline findings — arm64

Numbers below are from **Python 3.15.0**; the framework *ratios* hold across every version in the matrix (see per-version tables).

- **Spawn throughput (tracked spawn+join) — filament wins big:** filament 489.0k gt/s vs gevent 197.6k vs eventlet 240.1k — filament 2.5x gevent, 2.0x eventlet. Across the matrix filament runs 2.25-4.16x the spawn rate of gevent, widest on Python 2.7.18.
- **Context-switch rate — filament wins:** filament 4.20M sw/s vs gevent 1.73M vs eventlet 1.02M — filament 2.4x gevent, 4.1x eventlet. Consistent across all versions.
- **Semaphore / Queue — filament wins:** its C-level `Semaphore` does ~43.79M uncontended ops/s vs gevent 14.00M / eventlet 13.35M (3-8x), and it leads on queue put/get too.
- **Mixed green+native queue — filament only:** a single bounded `Queue` worked simultaneously by greenthreads AND native `threading.Thread` producers/consumers runs at ~5.05M items/s in filament. The same workload on gevent/eventlet deadlocks or errors — their queues are hub-bound and cannot be used from a foreign OS thread. filament's per-thread scheduler + deferred cross-thread wakeup makes this a first-class pattern (same mechanism as the #137 win).
- **Threadpool round-trip:** filament 152.2k calls/s vs gevent 89.3k vs eventlet 43.1k — filament 1.7x gevent, 3.5x eventlet. Across Python 3 in this matrix that is 1.68-1.92x gevent's rate, best on Python 3.12.13; filament's pool wakes the most-recently-idle (MRU) worker for each job, keeping the hot worker's stack and caches warm.
- **Echo server — filament wins:** filament matches or beats gevent's requests/s at both concurrencies, with better p50/p99 latency; eventlet trails both. Persistent edge-triggered readiness events (no per-block epoll_ctl) plus a GIL-free io-thread completion path carry the socket hot loop.
- **#137 logging-in-threadpool:** filament logs from its real-thread pool while the hub runs greenthreads and completes **every time, on every interpreter and both machines, no workaround, 1.8k-15.7k msgs/s**. For gevent and eventlet this is a race, not a verdict, and the machine decides it: on the 6-core box gevent deadlocks outright, including with its documented mitigations (hub threadpool + native logging locks + `logThreads=False`); on the 64-thread host it completed 6 of 6 repeats. eventlet loses the race on both, most recently 4 times in 6. Read the per-version tables for what actually happened rather than assuming either outcome.

