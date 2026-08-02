# filament vs gevent vs eventlet — benchmark results

Higher is better on every row except the latency rows, where lower is better. Cell values other than a number:

| Cell | Meaning |
|---|---|
| **error** | the framework raised; the benchmark did not complete |
| **deadlock** | printed nothing for the whole idle timeout and was killed |
| **crash** | exited without printing a result |
| **segfault** / **signal** | killed by a fatal signal |
| n/a | not run for this interpreter |
| † | the row crosses OS threads, so its absolute value moves with thread placement |
| not available | the library could not be installed on this interpreter |
| echo, driven remotely | server only, driven from a second machine by one fixed Go generator; mean of 3 alternated reps |
| echo, in-process | client and server on one runtime in one process (no second host available for that row) |

## Environments

| Arch | Python | greenlet | gevent | eventlet | host | measured |
|---|---|---|---|---|---|---|
| amd64 | 3.15.0 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-08-01 (a25ac2d) |
| amd64 | 3.14.6 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-08-01 (a25ac2d) |
| amd64 | 3.13.14 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-08-01 (a25ac2d) |
| amd64 | 3.12.13 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-08-01 (a25ac2d) |
| amd64 | 3.11.15 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-08-01 (a25ac2d) |
| amd64 | 3.10.20 | 3.3.2 | 26.7.0 | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-08-01 (a25ac2d) |
| amd64 | 3.9.25 | 3.2.5 | 26.7.0 | 0.40.4 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-08-01 (a25ac2d) |
| amd64 free-threaded | 3.14.6 | 3.3.2 | not available | 0.41.1 | AMD Ryzen Threadripper PRO 5975WX, 32c/64t | 2026-08-01 (a25ac2d) |
| arm64 | 3.15.0 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5 Max, 18c, bare metal | 2026-08-01 (a25ac2d) |
| arm64 | 3.14.6 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5 Max, 18c, bare metal | 2026-08-01 (a25ac2d) |
| arm64 | 3.13.14 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5 Max, 18c, bare metal | 2026-08-01 (a25ac2d) |
| arm64 | 3.12.13 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5 Max, 18c, bare metal | 2026-08-01 (a25ac2d) |
| arm64 | 3.11.15 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5 Max, 18c, bare metal | 2026-08-01 (a25ac2d) |
| arm64 | 3.10.20 | 3.3.2 | 26.7.0 | 0.41.1 | Apple M5 Max, 18c, bare metal | 2026-08-01 (a25ac2d) |
| arm64 | 3.9.25 | 3.2.5 | 26.7.0 | 0.40.4 | Apple M5 Max, 18c, bare metal | 2026-08-01 (a25ac2d) |
| arm64 | 2.7.18 | 2.0.2 | 22.10.2 | 0.33.3 | 6 cpus, container | 2026-07-29 (2210904) |
| arm64 free-threaded | 3.14.6 | 3.3.2 | not available | 0.41.1 | Apple M5 Max, 18c, bare metal | 2026-08-01 (a25ac2d) |

## amd64 · Python 3.15.0

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 277.2k | 96.7k | 109.7k |
| spawn (fire & forget) | spawn_n/s | 256.3k | 199.7k | 180.8k |
| context switch | switches/s | 2.06M | 819.3k | 489.3k |
| semaphore uncontended | ops/s | 17.99M | 6.90M | 6.75M |
| semaphore contended | ops/s | 19.00M | 6.93M | 6.58M |
| queue put/get | items/s | 6.34M | 6.50M | 3.53M |
| queue shared green+native threads | items/s | 3.57M | **error** | **deadlock** |
| tpool round-trip | calls/s | 105.5k | 57.0k | 25.8k |
| tpool round-trip | mean latency | 9.48 us | 17.55 us | 38.8 us |
| echo, driven remotely @ conc 200 | req/s | 180.7k | 111.0k | 81.0k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 1.115 / 1.392 | 1.769 / 2.18 | 2.457 / 4.552 |
| echo, driven remotely @ conc 1000 | req/s | 181.1k | 104.8k | 72.0k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 5.408 / 6.712 | 9.354 / 18.254 | 13.84 / 26.202 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 177.7k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 92.5k msg/s |
| gevent | workaround | **deadlock** | - |
| eventlet | naive | **deadlock** | - |

## amd64 · Python 3.14.6

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 267.3k | 98.6k | 115.7k |
| spawn (fire & forget) | spawn_n/s | 253.1k | 215.3k | 194.5k |
| context switch | switches/s | 2.08M | 814.4k | 481.4k |
| semaphore uncontended | ops/s | 18.25M | 7.22M | 7.48M |
| semaphore contended | ops/s | 20.07M | 7.39M | 7.07M |
| queue put/get | items/s | 7.96M | 6.65M | 3.53M |
| queue shared green+native threads | items/s | 3.31M | **error** | **error** |
| tpool round-trip | calls/s | 107.5k | 55.0k | 26.3k |
| tpool round-trip | mean latency | 9.3 us | 18.18 us | 37.97 us |
| echo, driven remotely @ conc 200 | req/s | 171.6k | 111.2k | 85.7k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 1.205 / 1.415 | 1.79 / 2.78 | 2.332 / 4.252 |
| echo, driven remotely @ conc 1000 | req/s | 184.2k | 104.1k | 76.3k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 5.339 / 6.387 | 9.555 / 17.367 | 13.058 / 24.682 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 154.7k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 127.9k msg/s |
| gevent | workaround | **deadlock** | - |
| eventlet | eventlet.tpool | OK — completed | 1.8k msg/s |

## amd64 · Python 3.13.14

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 277.5k | 101.3k | 115.1k |
| spawn (fire & forget) | spawn_n/s | 263.1k | 218.6k | 198.3k |
| context switch | switches/s | 2.25M | 816.0k | 491.5k |
| semaphore uncontended | ops/s | 23.21M | 7.50M | 6.51M |
| semaphore contended | ops/s | 21.31M | 7.52M | 6.54M |
| queue put/get | items/s | 8.63M | 7.27M | 3.30M |
| queue shared green+native threads | items/s | 3.31M | **error** | **deadlock** |
| tpool round-trip | calls/s | 246.2k | 85.7k | 24.2k |
| tpool round-trip | mean latency | 4.06 us | 11.67 us | 41.33 us |
| echo, driven remotely @ conc 200 | req/s | 179.6k | 106.4k | 85.8k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 1.066 / 1.409 | 1.835 / 2.175 | 2.328 / 4.29 |
| echo, driven remotely @ conc 1000 | req/s | 177.3k | 100.7k | 74.3k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 5.661 / 6.836 | 9.75 / 18.492 | 13.371 / 25.579 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 65.4k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 6.4k msg/s |
| gevent | workaround | **deadlock** | - |
| eventlet | naive | **deadlock** | - |

## amd64 · Python 3.12.13

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 239.4k | 93.2k | 99.5k |
| spawn (fire & forget) | spawn_n/s | 225.1k | 188.4k | 160.7k |
| context switch | switches/s | 2.32M | 803.2k | 479.1k |
| semaphore uncontended | ops/s | 16.51M | 7.27M | 6.49M |
| semaphore contended | ops/s | 21.95M | 7.13M | 6.71M |
| queue put/get | items/s | 8.87M | 6.77M | 2.96M |
| queue shared green+native threads | items/s | 3.10M | **error** | **deadlock** |
| tpool round-trip | calls/s | 100.0k | 53.6k | 24.2k |
| tpool round-trip | mean latency | 10.0 us | 18.65 us | 41.24 us |
| echo, driven remotely @ conc 200 | req/s | 191.7k | 109.5k | 77.8k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 1.025 / 1.337 | 1.81 / 2.102 | 2.536 / 4.858 |
| echo, driven remotely @ conc 1000 | req/s | 174.2k | 103.5k | 70.0k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 5.951 / 6.82 | 9.544 / 18.105 | 14.098 / 28.856 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 153.9k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 12.5k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 23.5k msg/s |
| eventlet | eventlet.tpool | OK — completed | 2.2k msg/s |

## amd64 · Python 3.11.15

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 245.7k | 90.8k | 100.9k |
| spawn (fire & forget) | spawn_n/s | 223.0k | 196.8k | 170.0k |
| context switch | switches/s | 2.19M | 956.0k | 485.0k |
| semaphore uncontended | ops/s | 20.12M | 7.68M | 5.84M |
| semaphore contended | ops/s | 20.42M | 7.65M | 5.74M |
| queue put/get | items/s | 8.63M | 6.31M | 2.63M |
| queue shared green+native threads | items/s | 3.12M | **error** | **deadlock** |
| tpool round-trip | calls/s | 102.1k | 56.5k | 20.1k |
| tpool round-trip | mean latency | 9.79 us | 17.7 us | 49.81 us |
| echo, driven remotely @ conc 200 | req/s | 185.5k | 107.8k | 75.1k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 1.042 / 1.323 | 1.817 / 2.068 | 2.653 / 5.072 |
| echo, driven remotely @ conc 1000 | req/s | 176.8k | 101.4k | 68.5k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 5.695 / 6.8 | 9.701 / 18.865 | 14.431 / 29.266 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 151.8k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 7.2k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 2.2k msg/s |
| eventlet | naive | **deadlock** | - |

## amd64 · Python 3.10.20

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 457.8k | 125.9k | 98.4k |
| spawn (fire & forget) | spawn_n/s | 425.6k | 323.5k | 199.9k |
| context switch | switches/s | 1.99M | 831.6k | 366.8k |
| semaphore uncontended | ops/s | 16.63M | 7.56M | 3.73M |
| semaphore contended | ops/s | 15.25M | 7.27M | 3.49M |
| queue put/get | items/s | 7.12M | 6.51M | 1.64M |
| queue shared green+native threads | items/s | 2.91M | **error** | **deadlock** |
| tpool round-trip | calls/s | 98.2k | 44.5k | 14.6k |
| tpool round-trip | mean latency | 10.18 us | 22.48 us | 68.5 us |
| echo, driven remotely @ conc 200 | req/s | 182.6k | 96.5k | 59.6k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 1.054 / 1.412 | 2.07 / 2.374 | 3.309 / 6.443 |
| echo, driven remotely @ conc 1000 | req/s | 189.2k | 93.9k | 53.1k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 5.276 / 6.252 | 10.419 / 12.025 | 18.605 / 37.409 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 118.9k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 3.6k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 14.1k msg/s |
| eventlet | naive | **deadlock** | - |

## amd64 · Python 3.9.25

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 476.5k | 114.9k | 93.6k |
| spawn (fire & forget) | spawn_n/s | 421.4k | 288.6k | 198.9k |
| context switch | switches/s | 1.62M | 861.1k | 371.3k |
| semaphore uncontended | ops/s | 18.78M | 7.58M | 3.28M |
| semaphore contended | ops/s | 16.61M | 7.34M | 2.91M |
| queue put/get | items/s | 7.50M | 6.55M | 1.51M |
| queue shared green+native threads | items/s | 2.96M | **error** | **deadlock** |
| tpool round-trip | calls/s | 93.5k | 40.8k | 15.5k |
| tpool round-trip | mean latency | 10.7 us | 24.51 us | 64.64 us |
| echo, driven remotely @ conc 200 | req/s | 172.9k | 98.9k | 60.2k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 1.178 / 1.415 | 1.994 / 2.3 | 3.303 / 6.31 |
| echo, driven remotely @ conc 1000 | req/s | 169.3k | 95.8k | 54.3k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 5.88 / 6.989 | 10.243 / 14.104 | 18.232 / 36.071 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 119.2k msg/s |
| gevent | gevent.threadpool.ThreadPool | OK — completed | 1.9k msg/s |
| gevent | gevent.get_hub().threadpool + native locks | OK — completed | 14.3k msg/s |
| eventlet | eventlet.tpool | OK — completed | 4.0k msg/s |

## amd64 free-threaded · Python 3.14.6

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 269.1k | **error** | **segfault** |
| spawn (fire & forget) | spawn_n/s | 246.3k | **error** | **segfault** |
| context switch | switches/s | 1.32M | **error** | 448.5k |
| semaphore uncontended | ops/s | 20.12M | **error** | 6.75M |
| semaphore contended | ops/s | 16.40M | **error** | 6.45M |
| queue put/get | items/s | 7.03M | **error** | 3.33M |
| queue shared green+native threads | items/s | 1.25M | **error** | **deadlock** |
| tpool round-trip | calls/s | 95.0k | **error** | 25.5k |
| tpool round-trip | mean latency | 10.53 us | **error** | 39.18 us |
| echo, driven remotely @ conc 200 | req/s | 167.0k | **error** | 81.2k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 1.176 / 1.486 | **error** | 2.454 / 4.494 |
| echo, driven remotely @ conc 1000 | req/s | 165.6k | **error** | 75.6k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 5.735 / 7.208 | **error** | 13.201 / 24.495 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 152.9k msg/s |
| gevent | naive | **error** | - |
| gevent | workaround | **error** | - |
| eventlet | naive | **segfault** | - |

### filament: GIL off vs GIL on (same host, Python 3.14.6 both sides)

| Benchmark | Metric | GIL off | GIL on | off/on |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 269.1k | 267.3k | 1.01x |
| spawn (fire & forget) | spawn_n/s | 246.3k | 253.1k | 0.97x |
| context switch | switches/s | 1.32M | 2.08M | 0.63x |
| semaphore uncontended | ops/s | 20.12M | 18.25M | 1.10x |
| semaphore contended | ops/s | 16.40M | 20.07M | 0.82x |
| queue put/get | items/s | 7.03M | 7.96M | 0.88x |
| queue shared green+native threads † | items/s | 1.25M | 3.31M | 0.38x |
| tpool round-trip † | calls/s | 95.0k | 107.5k | 0.88x |
| #137 logging from threadpool † | msgs/s | 152.9k | 154.7k | 0.99x |
| echo, driven remotely @ conc 200 † | req/s | 167.0k | 171.6k | 0.97x |
| echo, driven remotely @ conc 1000 † | req/s | 165.6k | 184.2k | 0.90x |

## arm64 · Python 3.15.0

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 465.3k | 196.1k | 225.4k |
| spawn (fire & forget) | spawn_n/s | 462.2k | 379.6k | 333.8k |
| context switch | switches/s | 4.21M | 1.71M | 994.0k |
| semaphore uncontended | ops/s | 43.37M | 13.92M | 13.48M |
| semaphore contended | ops/s | 42.14M | 13.49M | 13.18M |
| queue put/get | items/s | 15.00M | 12.36M | 6.04M |
| queue shared green+native threads | items/s | 5.41M | **error** | **deadlock** |
| tpool round-trip | calls/s | 160.0k | 90.4k | 38.4k |
| tpool round-trip | mean latency | 6.25 us | 11.06 us | 26.04 us |
| echo, driven remotely @ conc 200 | req/s | 319.8k | 210.3k | 162.2k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 0.619 / 0.863 | 0.791 / 1.641 | 1.22 / 1.56 |
| echo, driven remotely @ conc 1000 | req/s | 300.4k | 198.0k | 147.4k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 3.305 / 3.98 | 4.903 / 9.778 | 6.741 / 7.425 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 2.5k msg/s |
| gevent | naive | **deadlock** | - |
| gevent | workaround | **deadlock** | - |
| eventlet | naive | **deadlock** | - |

## arm64 · Python 3.14.6

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 461.7k | 195.8k | 226.3k |
| spawn (fire & forget) | spawn_n/s | 460.4k | 387.4k | 332.6k |
| context switch | switches/s | 4.38M | 1.70M | 1.01M |
| semaphore uncontended | ops/s | 44.18M | 14.30M | 14.87M |
| semaphore contended | ops/s | 42.26M | 13.59M | 13.95M |
| queue put/get | items/s | 14.67M | 12.12M | 6.40M |
| queue shared green+native threads | items/s | 5.16M | **error** | **deadlock** |
| tpool round-trip | calls/s | 158.7k | 90.2k | 41.9k |
| tpool round-trip | mean latency | 6.3 us | 11.09 us | 23.84 us |
| echo, driven remotely @ conc 200 | req/s | 323.3k | 210.2k | 164.0k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 0.615 / 0.73 | 0.792 / 1.657 | 1.211 / 1.372 |
| echo, driven remotely @ conc 1000 | req/s | 300.2k | 198.2k | 146.9k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 3.306 / 3.782 | 4.924 / 9.835 | 6.758 / 7.314 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 1.8k msg/s |
| gevent | naive | **deadlock** | - |
| gevent | workaround | **deadlock** | - |
| eventlet | naive | **deadlock** | - |

## arm64 · Python 3.13.14

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 467.6k | 201.1k | 232.7k |
| spawn (fire & forget) | spawn_n/s | 469.5k | 400.4k | 355.9k |
| context switch | switches/s | 4.64M | 1.71M | 1.06M |
| semaphore uncontended | ops/s | 48.03M | 15.08M | 13.39M |
| semaphore contended | ops/s | 44.80M | 14.67M | 13.41M |
| queue put/get | items/s | 15.61M | 12.72M | 6.01M |
| queue shared green+native threads | items/s | 4.87M | **error** | **deadlock** |
| tpool round-trip | calls/s | 155.2k | 90.7k | 41.8k |
| tpool round-trip | mean latency | 6.44 us | 11.03 us | 23.9 us |
| echo, driven remotely @ conc 200 | req/s | 323.5k | 211.1k | 163.9k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 0.614 / 0.753 | 0.789 / 1.643 | 1.213 / 1.334 |
| echo, driven remotely @ conc 1000 | req/s | 303.1k | 198.4k | 147.3k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 3.283 / 3.661 | 4.915 / 9.814 | 6.743 / 7.341 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 1.8k msg/s |
| gevent | naive | **deadlock** | - |
| gevent | workaround | **deadlock** | - |
| eventlet | naive | **deadlock** | - |

## arm64 · Python 3.12.13

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 428.5k | 191.6k | 221.0k |
| spawn (fire & forget) | spawn_n/s | 415.8k | 365.0k | 303.3k |
| context switch | switches/s | 4.56M | 1.72M | 1.05M |
| semaphore uncontended | ops/s | 44.06M | 14.81M | 13.31M |
| semaphore contended | ops/s | 45.23M | 14.25M | 13.81M |
| queue put/get | items/s | 15.50M | 12.66M | 5.56M |
| queue shared green+native threads | items/s | 4.84M | **error** | **deadlock** |
| tpool round-trip | calls/s | 157.7k | 88.5k | 41.8k |
| tpool round-trip | mean latency | 6.34 us | 11.29 us | 23.9 us |
| echo, driven remotely @ conc 200 | req/s | 322.7k | 207.9k | 162.2k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 0.616 / 0.696 | 0.802 / 1.676 | 1.224 / 1.456 |
| echo, driven remotely @ conc 1000 | req/s | 304.0k | 197.0k | 145.4k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 3.269 / 4.141 | 4.945 / 9.876 | 6.808 / 9.331 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 1.9k msg/s |
| gevent | naive | **deadlock** | - |
| gevent | workaround | **deadlock** | - |
| eventlet | naive | **deadlock** | - |

## arm64 · Python 3.11.15

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 460.0k | 187.2k | 225.4k |
| spawn (fire & forget) | spawn_n/s | 435.8k | 385.9k | 329.5k |
| context switch | switches/s | 4.47M | 1.94M | 1.11M |
| semaphore uncontended | ops/s | 40.60M | 16.25M | 12.83M |
| semaphore contended | ops/s | 41.48M | 15.08M | 13.23M |
| queue put/get | items/s | 16.51M | 12.78M | 5.56M |
| queue shared green+native threads | items/s | 4.81M | **error** | **deadlock** |
| tpool round-trip | calls/s | 176.7k | 91.7k | 42.9k |
| tpool round-trip | mean latency | 5.66 us | 10.91 us | 23.3 us |
| echo, driven remotely @ conc 200 | req/s | 323.6k | 205.6k | 158.4k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 0.615 / 0.685 | 0.833 / 1.713 | 1.252 / 1.406 |
| echo, driven remotely @ conc 1000 | req/s | 301.1k | 193.9k | 144.4k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 3.302 / 4.046 | 5.042 / 10.063 | 6.863 / 8.757 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 2.1k msg/s |
| gevent | naive | **deadlock** | - |
| gevent | workaround | **deadlock** | - |
| eventlet | naive | **deadlock** | - |

## arm64 · Python 3.10.20

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.14M | 298.4k | 247.8k |
| spawn (fire & forget) | spawn_n/s | 1.06M | 773.1k | 462.9k |
| context switch | switches/s | 4.27M | 1.59M | 729.1k |
| semaphore uncontended | ops/s | 37.13M | 15.50M | 7.44M |
| semaphore contended | ops/s | 34.24M | 14.98M | 7.19M |
| queue put/get | items/s | 13.28M | 11.53M | 3.07M |
| queue shared green+native threads | items/s | 4.54M | **error** | **deadlock** |
| tpool round-trip | calls/s | 157.9k | 93.2k | 36.2k |
| tpool round-trip | mean latency | 6.33 us | 10.73 us | 27.61 us |
| echo, driven remotely @ conc 200 | req/s | 323.7k | 196.0k | 132.6k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 0.614 / 0.719 | 0.907 / 1.844 | 1.497 / 1.709 |
| echo, driven remotely @ conc 1000 | req/s | 307.0k | 185.4k | 117.9k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 3.234 / 3.994 | 5.272 / 10.512 | 8.407 / 10.975 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 2.6k msg/s |
| gevent | naive | **deadlock** | - |
| gevent | workaround | **deadlock** | - |
| eventlet | naive | **deadlock** | - |

## arm64 · Python 3.9.25

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 1.18M | 309.2k | 237.7k |
| spawn (fire & forget) | spawn_n/s | 1.06M | 705.6k | 461.6k |
| context switch | switches/s | 3.34M | 1.74M | 768.6k |
| semaphore uncontended | ops/s | 41.48M | 16.88M | 6.38M |
| semaphore contended | ops/s | 32.49M | 15.34M | 5.92M |
| queue put/get | items/s | 12.70M | 11.44M | 2.70M |
| queue shared green+native threads | items/s | 4.57M | **error** | **deadlock** |
| tpool round-trip | calls/s | 137.1k | 82.1k | 37.0k |
| tpool round-trip | mean latency | 7.29 us | 12.18 us | 27.02 us |
| echo, driven remotely @ conc 200 | req/s | 316.5k | 200.3k | 133.2k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 0.623 / 0.826 | 0.862 / 1.787 | 1.477 / 2.156 |
| echo, driven remotely @ conc 1000 | req/s | 295.5k | 188.7k | 121.9k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 3.324 / 4.766 | 5.131 / 10.23 | 8.075 / 12.022 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 1.9k msg/s |
| gevent | naive | **deadlock** | - |
| gevent | workaround | **deadlock** | - |
| eventlet | naive | **deadlock** | - |

## arm64 · Python 2.7.18

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
| echo, in-process @ conc 100 | req/s | 126.4k | 100.7k | 57.6k |
| echo, in-process @ conc 100 | p50/p99 ms | 0.762 / 0.968 | 0.966 / 1.193 | 1.673 / 2.905 |
| echo, in-process @ conc 1000 | req/s | 103.3k | 82.0k | 35.9k |
| echo, in-process @ conc 1000 | p50/p99 ms | 7.853 / 12.209 | 10.417 / 24.28 | 24.754 / 44.676 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 15.7k msg/s |
| gevent | naive | **deadlock** | - |
| gevent | workaround | **deadlock** | - |
| eventlet | naive | **deadlock** | - |

## arm64 free-threaded · Python 3.14.6

| Benchmark | Metric | filament | gevent | eventlet |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 492.4k | **error** | **segfault** |
| spawn (fire & forget) | spawn_n/s | 481.6k | **error** | **segfault** |
| context switch | switches/s | 3.38M | **error** | 970.7k |
| semaphore uncontended | ops/s | 34.50M | **error** | 13.60M |
| semaphore contended | ops/s | 31.40M | **error** | 13.34M |
| queue put/get | items/s | 12.49M | **error** | 5.52M |
| queue shared green+native threads | items/s | 2.68M | **error** | **deadlock** |
| tpool round-trip | calls/s | 137.2k | **error** | 44.1k |
| tpool round-trip | mean latency | 7.29 us | **error** | 22.7 us |
| echo, driven remotely @ conc 200 | req/s | 313.9k | **error** | 162.9k |
| echo, driven remotely @ conc 200 | p50/p99 ms | 0.632 / 0.77 | **error** | 1.22 / 1.356 |
| echo, driven remotely @ conc 1000 | req/s | 292.2k | **error** | 142.6k |
| echo, driven remotely @ conc 1000 | p50/p99 ms | 3.403 / 3.864 | **error** | 6.955 / 8.372 |

### #137 logging-from-threadpool (monkey-patched)

| Framework | Path | Result | throughput |
|---|---|---|---|
| filament | filament.tpool | OK — completed | 155.4k msg/s |
| gevent | naive | **error** | - |
| gevent | workaround | **error** | - |
| eventlet | naive | **segfault** | - |

### filament: GIL off vs GIL on (same host, Python 3.14.6 both sides)

| Benchmark | Metric | GIL off | GIL on | off/on |
|---|---|---|---|---|
| spawn (tracked, spawn+join) | greenthreads/s | 492.4k | 461.7k | 1.07x |
| spawn (fire & forget) | spawn_n/s | 481.6k | 460.4k | 1.05x |
| context switch | switches/s | 3.38M | 4.38M | 0.77x |
| semaphore uncontended | ops/s | 34.50M | 44.18M | 0.78x |
| semaphore contended | ops/s | 31.40M | 42.26M | 0.74x |
| queue put/get | items/s | 12.49M | 14.67M | 0.85x |
| queue shared green+native threads † | items/s | 2.68M | 5.16M | 0.52x |
| tpool round-trip † | calls/s | 137.2k | 158.7k | 0.86x |
| #137 logging from threadpool † | msgs/s | 155.4k | 1.8k | 84.67x |
| echo, driven remotely @ conc 200 † | req/s | 313.9k | 323.3k | 0.97x |
| echo, driven remotely @ conc 1000 † | req/s | 292.2k | 300.2k | 0.97x |

