# Benchmark methodology and caveats

`RESULTS.md` is tables only. This is everything else: what each benchmark
actually does, how the runs are isolated, which numbers can be compared with
which, and how to re-run the matrix without invalidating it.

Greenlet-based cooperative concurrency shootout: **filament** (this repo)
against modern **gevent** and **eventlet**, across CPython versions on aarch64
and x86_64 Linux, plus a free-threaded (PEP 703) build. Results are grouped by
architecture and by GIL mode.

Each (framework, benchmark) pair runs in its own fresh subprocess.
Micro-benchmarks report the **median** of several timed reps (warm-up
discarded) from a monotonic clock.

## What each benchmark does

- Same logical workload run three ways (filament / gevent / eventlet) with
  identical sizes per framework. For filament the in-process client is
  `filament.socket`; gevent uses `gevent.socket` + a `StreamServer`-style accept
  loop; eventlet uses `eventlet.green.socket`. The echo client stays in the same
  framework as the server for fairness.
- Each pair runs in a **fresh interpreter subprocess**, so monkey-patching and
  hub state never leak between frameworks.
- **spawn** = 100k greenthreads spawned then joined. **context switch** = 100
  greenthreads x 10k `sleep(0)` = 1,000,000 switches. **semaphore uncontended**
  = 1M acquire/release on one greenthread; **contended** = 50 greenthreads on a
  `Semaphore(1)`. **queue** = 200k producer/consumer items. **tpool** = 3000
  sequential real-thread round-trips. **echo** = concurrency 100 (x100
  round-trips) and 1000 (x20), 64-byte payload.
- **queue mixed** = ONE bounded queue (maxsize 100) shared simultaneously by a
  greenthread producer + consumer AND a native `threading.Thread` producer +
  consumer (50k items per producer), so native threads block in `q.get()` /
  `q.put()` while greenthreads work the same queue. gevent and eventlet queues
  are hub-bound; foreign-OS-thread use is undefined for them and runs under the
  deadlock watchdog.
- **#137** = monkey-patch everything, then log heavily from real OS-thread pool
  workers while greenthreads spin in the hub. Each attempt runs under a 45 s
  **idle** watchdog: the worker reports progress as it logs, so a run that keeps
  making progress is allowed to finish however slow the host, while one that
  goes silent is killed and recorded as **deadlock**. Whether gevent/eventlet
  hang here depends on the machine -- it is a race between the hub and the
  logging lock, and a faster host with more cores wins it more often -- so a
  single cell is one roll of the dice, not a property of the library. filament
  has not lost it on any machine or interpreter.

A "deadlock" cell means only that the worker printed nothing for the whole idle
timeout. Nothing detects an actual deadlock. On a new or slow platform a cell
can be merely slow and get labelled a hang, so before believing one, raise the
budget with `--timeout-scale` and see whether it completes.

## Reading the numbers

**Compare within a table, not across tables.** All three frameworks in one
table ran back to back on one host under identical conditions; that ratio is the
signal. Anything else is a comparison of two runs.

**The OS-thread caveat.** `tpool` and `#137` cross into real OS threads, and on
a many-core host their absolute numbers are not reproducible: the amd64 box
(32c/64t) gives a clean bimodal split ~1.6x apart, switching even between reps
inside one process. It is thread placement, and `taskset` proves it -- pinned to
a single CPU the same benchmark repeats to ~2% (filament 50-52k, gevent 38-39k
calls/s), pinned to two it is faster and mostly steady, and turned loose on all
32 it oscillates. The 1.6x factor hits both runtimes equally, so the *ranking*
holds even where the absolute value does not: filament leads gevent by 1.3-1.4x
in every pinned configuration. Read a single `tpool` or `#137` cell as an order
of magnitude; the pure-greenthread rows repeat to within a few percent.

**filament's echo row still varies more than gevent's on the amd64 box**, even
with the client starts staggered: across the three reps behind one cell,
filament spans 1.25-1.85x while gevent holds 1.02-1.05x. On macOS that spread
had a specific cause and is now fixed (see below); on the 64-thread Linux box it
looks like the same thread-placement effect as `tpool`, which filament is
exposed to and the single-threaded runtimes are not. Compare filament's echo
against gevent's *within* one table, and do not read a 30% move in one filament
echo cell between two runs as a change in filament.

**Cross-version caveat.** Each Python version's table was recorded in its own
sequential run on the box named for it in the Environments table -- note that the
arm64 2.7 row comes from a Linux container while every other arm64 row is the
Apple host, so 2.7 is not comparable to them. Interpreter speed differs across
versions, so absolute numbers are **not** comparable across Python versions.

### Why the echo benchmark staggers its client starts

`echo_start_wave` (default 50) yields to the scheduler every 50 client starts
instead of firing them all at once. It exists because without it the
concurrency-1000 cell on macOS measured the operating system rather than the
echo server, and reported filament at 52k req/s against gevent's 91k. With it,
the same cell reads **130-149k against gevent's 81-87k** and repeats to within
5%. This is the whole story, because the trap is a general one.

*The cell was mostly not measuring echo.* `_echo_once` times the whole run --
spawning the greenthreads, connecting all of them, then the round-trips -- and
reports that as throughput, while p50/p99 cover only the round-trips. At
concurrency 1000 the connect phase was **26-96%** of the wall clock. That is how
the cell managed to report an excellent latency and a poor throughput at once:
by Little's law a 0.66 ms p50 at 52k req/s is ~35 requests in flight, which
cannot be true of 1000 busy connections, so the wall clock was going somewhere
the latency numbers did not look.

*macOS advances simultaneous loopback handshakes in batches.* A pure-stdlib
control -- plain non-blocking `connect()` plus `selectors.KqueueSelector`, no
filament and no gevent anywhere -- fires 1000 loopback connects at once and
completes them at p50 43 ms / max 77 ms, with **17-23 ms holes** between
consecutive completions. At 200 connects it is p50 4 ms with a single ~28 ms
hole. The hole stays ~30 ms whether there are 200 connections or 2000, which is
the signature of a timer rather than of per-connection work, and it belongs to
the OS.

*Whoever starts their clients fastest pays the most for it.* Eager greenthread
startup is exactly what filament beats gevent at (2.3-4.2x on spawn), so all
1000 clients called `connect()` in one burst and sat in those holes: connect p50
33 ms against gevent's 4.8 ms. gevent's hub trickles its greenlets out, never
formed a burst that large, and never paid. A `sleep(0)` every 50 starts takes
filament's connect p50 to **2.2 ms** and adds no wall clock of its own; a 0.5 ms
or 1 ms gap does no better, so the yield alone is the fix. Because the same code
path runs for all three frameworks, what changed is what is measured, not who is
favoured -- gevent's number barely moved, which is the control.

*It also caused the run-to-run bimodality.* Back to back, the previous 1000
connections were still winding down when the next 1000 started, enlarging the
effective burst; the modes alternated, and since the harness takes the median of
three consecutive reps it published the slow one. The recorded aarch64 3.14.6
min/median/max was 50.9k / 51.7k / 136.1k -- both modes were in the data. It is
now 129.3k / 134.6k / 135.7k.

*What it was not*, each tested rather than assumed: not the accept queue
overflowing (macOS caps it at `kern.ipc.somaxconn` = 128 whatever `listen()`
asks for, but the kernel's "listen queue overflow" counter never moved); not TCP
retransmits or drops (no TCP counter moved at all); not the GIL (the
free-threaded build was identical, connect p50 32.6 against 32.7 ms); not io
thread saturation (per-thread CPU was the same in fast and slow reps while the
wall clock differed 2.4x, so the extra time was idle); not garbage collection,
not fd reuse, and not the single acceptor greenthread.

**This is a real platform behaviour, not only a benchmark artifact.** An
application that opens a thousand loopback connections in a tight loop on macOS
will meet the same holes. The benchmark's job is to measure the echo server, so
it staggers; a program that genuinely needs a thousand connections at once
cannot.

**Check the `measured` column before comparing two tables.** It carries the date
and the filament commit. The tables are not all recorded from one build -- when
one architecture has been re-measured and another has not, a difference between
them is a difference in filament, not in the architecture.

## The networked echo benchmark (`netecho`)

The echo rows in `RESULTS.md` run the client and the server in **one process on
one runtime**, so what they report is a whole-runtime number: a fast client and
a fast server are indistinguishable in it, and both compete for the same GIL and
scheduler. `netecho` is the server-only measurement. It is not part of the
matrix -- it needs two machines -- and is run by hand.

- **The generator is neutral and fixed**: `netecho/loadgen.go`, one Go binary
  driving all three frameworks. If the client were the framework under test as
  well, the comparison would still be whole-stack, just distributed. Go rather
  than Python because the generator must not be the bottleneck.
- **Connection setup is outside the measured window**, along with a warmup.
  Counting setup as throughput is what made the in-process benchmark report the
  platform's TCP behaviour as if it were echo performance.
- **The generator host must not share hardware with the server host.** A VM on
  the machine under test is not a second host.
- **The link must not be the bottleneck, and this is worth measuring before
  believing any of it.** Over Wi-Fi the same pair of machines caps at 21k req/s
  with a 4.25 ms RTT -- below every framework -- and every result would be the
  network. Over 10Gbase-T it is 0.13 ms and the servers reach 300k+.
  `loadgen -serve` runs a Go echo server for exactly this check.

**Tune the generator's `-procs` before trusting anything.** This workload is one
tiny blocking round-trip per goroutine, so it is latency-bound and more Ps buy
only scheduler churn. On the 18-core Apple Silicon host (6 performance cores, 12
efficiency) the Go default of 18 costs **1.6x**: as the generator it drives
236.7k req/s at 18 and 382.6k at 3; as the reference server it serves 235.7k at
18 and 380.5k at 4. On the 64-thread Linux box it makes no difference at all
(351-362k across `-procs` 4 to 64). Sweep it against `loadgen -serve`, use the
value that maximises the ceiling, and check the `gomaxprocs` field the result
records.

**netecho is now the echo row in `RESULTS.md`.** Every interpreter in the matrix
was measured this way -- 8 per architecture, 16 tables -- and the per-version
tables render "echo, driven remotely" in place of the in-process rows. The one
exception is aarch64 2.7, which ran in a Linux container that no longer exists as
a second host; it keeps its in-process rows, labelled "echo, in-process". The
in-process benchmark still runs and its numbers stay in the JSON, they are just
not what the table shows.

Each cell is **three repeats with the framework order alternated inside one
session** (filament/gevent/eventlet, then reversed, then forward again), so
session drift cannot be attributed to whichever arm happened to run during it.
64-byte payload, 2 s warmup + 6 s measured window per repeat. The table carries
the mean; the JSON keeps min and max as well.

Across the whole matrix, filament leads **gevent by 1.51-2.01x** and **eventlet
by 1.93-3.56x**, with p50 and p99 ordered the same way in every cell. Typical
absolute figures at 200 connections: on thor (amd64) filament 172-192k req/s
against gevent 96-111k; on the M5 Max (arm64) filament 317-324k against gevent
196-211k.

Within a cell the three repeats agree closely -- median max/min 1.02 for all
three frameworks -- but filament's worst cell spans 1.17 against gevent's 1.08,
the same "filament varies more on the 64-thread box" signature the loopback rows
show, and the same suspect: it serves sockets from an io thread and is exposed
to that host's placement lottery where the single-threaded runtimes are not.

The harness ceiling is 362-380k (`loadgen -serve` on the same path, both ends
tuned), so every server is measured against a reference faster than itself. The
Apple host's filament sits at ~85% of it and is the one figure still partly
compressed by the harness; on thor there is 2x headroom.

## Availability

- **gevent on Python 2.7**: no cp27/aarch64 wheel exists and stock source builds
  fail under a modern GCC (Cython-generated C errors); where the 2.7 column
  shows gevent numbers they come from a locally-built older gevent. eventlet
  0.33.3 (pure-Python) and filament both build and run on 2.7.
- **gevent tpool on Python 2.7**: gevent **22.10.2** (the last py2.7 release)
  deadlocks in the threadpool round-trip benchmark on 2.7 -- reproducible even at
  small scale. Its predecessor 21.12.0 completed the same benchmark (~23.6k
  calls/s), so this is a gevent regression in its final py2.7 release, not a
  harness artifact.
- **gevent on macOS**: wheels exist only for cp312-cp315, so 3.9 through 3.11
  build it from source. That works, but gevent's build mutates its bundled
  `deps/c-ares` in place, so a *second* source build re-using the same cached
  sdist fails at the c-ares `configure` step. Install into one venv at a time
  with the sdist cache cleared (`--no-cache`) and it is fine.
- **filament** builds and runs every benchmark (including `#137`) on every
  interpreter in the matrix, 2.7 through 3.15, with a version-tagged `.so` per
  interpreter -- and on the free-threaded build too.

## Free-threading (PEP 703)

Free-threaded results live in `results/<arch>-ft/` rather than under a `3.14.6t`
version string, which would collide with stock 3.14.6 on the filename and break
the numeric version sort.

The GIL state is recorded at run time, not assumed. A free-threaded build still
runs *with* the GIL if something re-enables it (`PYTHON_GIL=1`, or an extension
that has not declared `Py_MOD_GIL_NOT_USED`), and a table recorded that way would
be measuring the stock runtime under a free-threaded label. Both the build flag
and `sys._is_gil_enabled()` go into the results JSON.

What the free-threaded tables have shown so far:

- **gevent cannot be installed at all.** gevent 26.7.0 publishes 46 files on
  PyPI and not one is a free-threaded (`cp3__t`) wheel, so the install falls back
  to a source build, which wants system development headers. Read the empty
  column as "no free-threaded wheel exists", not as a measurement of gevent.
- **eventlet segfaults**, rather than merely failing: SIGSEGV on `#137` on both
  architectures, and on `spawn` as well on amd64. Reproduced standalone, outside
  the harness.
- **filament completes every benchmark** with the GIL genuinely off.

### The GIL on/off table

Where the same interpreter version was recorded both ways on one host, the
report emits a `filament: GIL off vs GIL on` table. That pair is the one
comparison in the document that is meaningful across tables, because the two
runs differ in exactly one thing. It refuses to print a ratio unless both sides
have the same host string *and* the same benchmark sizes -- matching on (arch,
version) alone once had it dividing an aarch64 container by an Apple laptop and
calling the result 0.17x.

It is still a **single-scheduler** measurement: it prices the free-threaded
interpreter, not filament scaling across cores.

Rows marked **†** cross OS threads, so each side is one draw of the placement
lottery above and the ratio inherits it. Two of them were re-measured properly
on amd64 -- 4 repeats per side, arms alternated inside one session, 2026-08-01:

- **echo is a lottery and the single-draw ratio is not a GIL cost.** At
  concurrency 100 both sides are tight -- GIL-on 92.8-95.0k req/s against GIL-off
  85.9-90.2k -- so free-threading costs about **7%** there, not the 37% one draw
  suggested. At concurrency 1000 GIL-on held 74.0-77.4k while GIL-off split
  70.5/72.6/106.1/108.4k, so no single ratio is honest.
- **The mixed green+native queue really does cost that much.** It repeats
  tightly in both builds -- GIL-on 3.17-3.83M items/s, GIL-off 1.21-1.23M -- so
  **~0.39x is real**, and it is the expected direction: that benchmark has native
  OS threads and greenthreads working one queue, and with the GIL gone they
  genuinely run at once and contend for the queue's own mutex, where before the
  GIL serialised them for free.

And one the other way, on arm64 (macOS, 2026-08-01, 3 repeats a side):

- **`#137` runs about 90x faster without the GIL, on macOS only.** GIL-on 1.4k /
  1.8k / 1.5k msgs/s against GIL-off 153.3k / 145.5k / 142.1k -- tight on both
  sides, so the ~95x in the table is not a placement draw. It is macOS-specific:
  every GIL row on that host sits at 1.6-2.9k msgs/s while Linux runs the same
  workload at 114-164k, and dropping the GIL brings macOS straight to the Linux
  figure. That benchmark is real OS threads logging while greenthreads run, so
  what it was measuring on macOS was GIL handoff, and there is none to measure
  now. The same comparison on amd64 is 1.05x, because Linux never had the
  problem.

## Running the matrix

```sh
python benchmarks/run_all.py [--python /path/to/venv/bin/python]
                             [--benchmarks a,b,c] [--scale full|small]
                             [--timeout-scale N] [--arch NAME] [--report-only]
```

Results are written to `results/<arch>/<pyver>.json` (`<arch>-ft` for a
free-threaded interpreter, detected automatically), and `RESULTS.md` is
regenerated from every JSON on disk. `--report-only` rebuilds the report without
running anything. `FIL_BENCH_COMMIT=<sha>` stamps the commit when the tree was
shipped without its `.git` (e.g. by `git archive`).

A partial run (`--benchmarks`) merges into the existing file for that
interpreter rather than replacing it -- which also means a `--scale small` run
will quietly leave small-scale numbers in a full-scale file. Delete the file
first, or use a throwaway tree.

Four things that silently invalidate a run:

- **The open-file limit.** `echo` at concurrency 1000 needs thousands of
  descriptors. A non-interactive ssh on Linux typically gives 1024 and macOS
  gives 256, and every framework then fails the acceptor with "fd exhaustion".
  `ulimit -n 65536` first.
- **Leftover workers.** `run_all.py` puts each worker in its own session so a
  wedged one can be killed as a group, which also means killing the driver's
  shell does not stop it: it reparents to init and keeps benchmarking. Launch
  the whole run under `setsid` and kill by process group. Beware that
  `pkill -f benchmarks/run_all.py` matches the shell running the `pkill` -- use
  `benchmarks/[r]un_all.py` or kill by PID, then verify with `ps`.
- **Buffering.** Python block-buffers stdout to a file; without
  `PYTHONUNBUFFERED=1` a run in progress looks stalled, which is exactly how a
  second matrix once ran unnoticed alongside the first.
- **A busy box.** Everything above assumes the machine is otherwise idle. Check
  the load average before starting, not after.
