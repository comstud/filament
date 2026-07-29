"""One (framework, benchmark) measurement, printed as a single JSON line.

Invoked by run_all.py in a fresh subprocess so monkey-patching / hub state
never cross-contaminates.  Usage:

    python worker.py <framework> <benchmark> [--params '<json>']

<framework>  one of: filament | gevent | eventlet
<benchmark>  one of: spawn ctxswitch semaphore queue queue_mixed tpool echo logging137

The result JSON is written to stdout on the last line, prefixed with
"RESULT_JSON:".  All diagnostics go to stderr.  Any failure is reported as
{"status": "error", "error": "..."} so the driver can keep going.
"""
from __future__ import print_function

import json
import os
import socket as _stdsocket
import sys
import threading
import time
import traceback

# BENCH_DIR (parent of benchmarks/) holds the built filament package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from statistics import median
except ImportError:  # py2.7
    def median(xs):
        xs = sorted(xs)
        n = len(xs)
        if n == 0:
            return 0.0
        if n % 2:
            return xs[n // 2]
        return (xs[n // 2 - 1] + xs[n // 2]) / 2.0

perf = getattr(time, "perf_counter", time.time)

# ---------------------------------------------------------------------------
# Default workload sizes (overridable via --params).  Chosen so micro-benches
# run in well under a second each while still being large enough to be stable.
# ---------------------------------------------------------------------------
DEFAULTS = {
    "spawn_k": 100000,          # greenthreads spawned + joined
    "spawn_reps": 5,
    "ctx_greenthreads": 100,    # N greenthreads each doing ctx_iters sleep(0)
    "ctx_iters": 10000,         # -> 1,000,000 context switches
    "ctx_reps": 5,
    "sem_uncontended": 1000000, # acquire/release pairs, single greenthread
    "sem_contended_gt": 50,     # greenthreads contending on Semaphore(1)
    "sem_contended_ops": 4000,  # ops per greenthread -> 200k contended ops
    "sem_reps": 5,
    "queue_items": 200000,      # producer/consumer items
    "queue_reps": 5,
    "qmix_items": 50000,        # items per producer in the mixed bench (x2)
    "qmix_maxsize": 100,        # bounded queue so producers block on full
    "qmix_reps": 5,
    "tpool_calls": 3000,        # sequential thread round-trips
    "tpool_reps": 5,
    "echo_msg": 64,             # payload bytes
    "echo_reps": 3,
    "echo_specs": [[100, 100], [1000, 20]],  # [concurrency, roundtrips]
    "log_workers": 4,           # threadpool workers
    "log_msgs": 20000,          # log lines per worker
    "log_hub_greenthreads": 8,  # greenthreads spinning sleep(0) in the hub
}


# ===========================================================================
# Framework adapters
# ===========================================================================
class Env(object):
    """Uniform primitives over filament / gevent / eventlet."""

    name = None
    lib_version = None

    def spawn(self, fn, *a):
        raise NotImplementedError

    def joinall(self, handles):
        raise NotImplementedError

    def spawn_n(self, fn, *a):
        raise NotImplementedError

    def sleep(self, t):
        raise NotImplementedError

    def Semaphore(self, n):
        raise NotImplementedError

    def Queue(self, maxsize=None):
        raise NotImplementedError

    def tpool_execute(self, fn, *a):
        raise NotImplementedError

    def tpool_shutdown(self):
        pass

    def green_socket(self):
        raise NotImplementedError

    def run(self, body):
        """Run body() in whatever context the framework needs; return result."""
        return body()


class FilamentEnv(Env):
    name = "filament"

    def __init__(self):
        import filament
        import filament.tpool as tpool
        import filament.socket as fsocket
        self._f = filament
        self._tpool = tpool
        self._socket = fsocket
        try:
            import greenlet
            self.lib_version = "filament(greenlet %s)" % greenlet.__version__
        except Exception:
            self.lib_version = "filament"

    def spawn(self, fn, *a):
        return self._f.spawn(fn, *a)

    def joinall(self, handles):
        self._f.joinall(handles)

    def spawn_n(self, fn, *a):
        self._f.spawn_n(fn, *a)

    def sleep(self, t):
        self._f.sleep(t)

    def Semaphore(self, n):
        return self._f.Semaphore(n)

    def Queue(self, maxsize=None):
        if maxsize is None:
            return self._f.Queue()
        return self._f.Queue(maxsize=maxsize)

    def tpool_execute(self, fn, *a):
        return self._tpool.execute(fn, *a)

    def tpool_shutdown(self):
        try:
            self._tpool.shutdown()
        except Exception:
            pass

    def green_socket(self):
        return self._socket.socket()

    def run(self, body):
        # filament tpool + some primitives require a running scheduler, which
        # exists while a Filament is being waited on.  Run everything inside one.
        return self._f.spawn(body).wait()


class GeventEnv(Env):
    name = "gevent"

    def __init__(self):
        import gevent
        import gevent.pool
        import gevent.queue
        import gevent.lock
        import gevent.threadpool
        import gevent.socket
        self._g = gevent
        self._queue = gevent.queue
        self._lock = gevent.lock
        self._socket = gevent.socket
        self._tp = gevent.threadpool.ThreadPool(8)
        self.lib_version = gevent.__version__

    def spawn(self, fn, *a):
        return self._g.spawn(fn, *a)

    def joinall(self, handles):
        self._g.joinall(handles)

    def spawn_n(self, fn, *a):
        # spawn_raw is the fire-and-forget primitive (no Greenlet object).
        self._g.spawn_raw(fn, *a)

    def sleep(self, t):
        self._g.sleep(t)

    def Semaphore(self, n):
        return self._lock.Semaphore(n)

    def Queue(self, maxsize=None):
        if maxsize is None:
            return self._queue.Queue()
        return self._queue.Queue(maxsize)

    def tpool_execute(self, fn, *a):
        return self._tp.apply(fn, a)

    def tpool_shutdown(self):
        try:
            self._tp.kill()
        except Exception:
            pass

    def green_socket(self):
        return self._socket.socket()


class EventletEnv(Env):
    name = "eventlet"

    def __init__(self):
        import eventlet
        import eventlet.queue
        import eventlet.semaphore
        import eventlet.tpool
        import eventlet.green.socket as gsock
        self._e = eventlet
        self._queue = eventlet.queue
        self._sem = eventlet.semaphore
        self._tpool = eventlet.tpool
        self._socket = gsock
        self.lib_version = eventlet.__version__

    def spawn(self, fn, *a):
        return self._e.spawn(fn, *a)

    def joinall(self, handles):
        for h in handles:
            h.wait()

    def spawn_n(self, fn, *a):
        self._e.spawn_n(fn, *a)

    def sleep(self, t):
        self._e.sleep(t)

    def Semaphore(self, n):
        return self._sem.Semaphore(n)

    def Queue(self, maxsize=None):
        if maxsize is None:
            return self._queue.Queue()
        return self._queue.Queue(maxsize)

    def tpool_execute(self, fn, *a):
        return self._tpool.execute(fn, *a)

    def tpool_shutdown(self):
        pass

    def green_socket(self):
        return self._socket.socket()


def make_env(name):
    if name == "filament":
        return FilamentEnv()
    if name == "gevent":
        return GeventEnv()
    if name == "eventlet":
        return EventletEnv()
    raise ValueError("unknown framework %r" % name)


# ===========================================================================
# Timing helper
# ===========================================================================
def measure(work, reps, warmup=True):
    """work() performs some ops and returns the op count.  Returns per-second
    throughput stats across reps (median/min/max) plus raw samples."""
    if warmup:
        work()
    samples = []
    for _ in range(reps):
        t0 = perf()
        n = work()
        dt = perf() - t0
        if dt <= 0:
            dt = 1e-9
        samples.append(n / dt)
    return {
        "per_sec_median": median(samples),
        "per_sec_min": min(samples),
        "per_sec_max": max(samples),
        "reps": reps,
        "samples": [round(s, 1) for s in samples],
    }


# ===========================================================================
# Benchmarks
# ===========================================================================
def bench_spawn(env, p):
    k = p["spawn_k"]
    reps = p["spawn_reps"]

    def noop():
        return None

    def tracked():
        def body():
            hs = [env.spawn(noop) for _ in range(k)]
            env.joinall(hs)
            return k
        return env.run(body)

    tracked_stats = measure(tracked, reps)

    # fire-and-forget: spawn_n K, spin until a shared counter hits K.
    spawn_n_stats = None
    spawn_n_err = None
    try:
        state = {"c": 0}

        def inc():
            state["c"] += 1

        def fireforget():
            def body():
                state["c"] = 0
                for _ in range(k):
                    env.spawn_n(inc)
                # drive the scheduler until all fire-and-forget tasks ran
                while state["c"] < k:
                    env.sleep(0)
                return k
            return env.run(body)

        spawn_n_stats = measure(fireforget, max(3, reps - 2))
    except Exception as e:
        spawn_n_err = "%s: %s" % (type(e).__name__, e)

    return {
        "k": k,
        "tracked_spawn_per_sec": tracked_stats,
        "fireforget_spawn_n_per_sec": spawn_n_stats,
        "fireforget_error": spawn_n_err,
    }


def bench_ctxswitch(env, p):
    n = p["ctx_greenthreads"]
    iters = p["ctx_iters"]
    reps = p["ctx_reps"]
    total = n * iters

    def spinner():
        for _ in range(iters):
            env.sleep(0)

    def work():
        def body():
            hs = [env.spawn(spinner) for _ in range(n)]
            env.joinall(hs)
            return total
        return env.run(body)

    stats = measure(work, reps)
    return {"greenthreads": n, "iters_each": iters,
            "total_switches": total, "switches_per_sec": stats}


def bench_semaphore(env, p):
    reps = p["sem_reps"]

    # --- uncontended: one greenthread hammering Semaphore(1) ---
    un_ops = p["sem_uncontended"]

    def uncontended():
        def body():
            sem = env.Semaphore(1)
            acq = sem.acquire
            rel = sem.release
            for _ in range(un_ops):
                acq()
                rel()
            return un_ops
        return env.run(body)

    un_stats = measure(uncontended, reps)

    # --- contended: N greenthreads competing on Semaphore(1) ---
    c_gt = p["sem_contended_gt"]
    c_ops = p["sem_contended_ops"]
    total_c = c_gt * c_ops

    def contended():
        def body():
            sem = env.Semaphore(1)

            def worker():
                for _ in range(c_ops):
                    sem.acquire()
                    sem.release()
            hs = [env.spawn(worker) for _ in range(c_gt)]
            env.joinall(hs)
            return total_c
        return env.run(body)

    c_stats = measure(contended, reps)
    return {
        "uncontended_ops": un_ops,
        "uncontended_ops_per_sec": un_stats,
        "contended_greenthreads": c_gt,
        "contended_total_ops": total_c,
        "contended_ops_per_sec": c_stats,
    }


def bench_queue(env, p):
    items = p["queue_items"]
    reps = p["queue_reps"]

    def work():
        def body():
            q = env.Queue()
            got = {"n": 0}

            def producer():
                for i in range(items):
                    q.put(i)

            def consumer():
                for _ in range(items):
                    q.get()
                    got["n"] += 1
            hp = env.spawn(producer)
            hc = env.spawn(consumer)
            env.joinall([hp, hc])
            return got["n"]
        return env.run(body)

    stats = measure(work, reps)
    return {"items": items, "items_per_sec": stats}


def bench_queue_mixed(env, p):
    """One bounded queue shared by greenthread AND native-thread producers
    and consumers simultaneously.  This is the cross-domain hand-off case:
    a native thread blocking in q.get()/q.put() while greenthreads do the
    same on the other end.  filament's deferred cross-thread wakeup makes
    this a supported pattern; gevent/eventlet queues are hub-bound and
    using them from a foreign OS thread is undefined (expect deadlock or
    a cross-thread switch error)."""
    items = p["qmix_items"]
    maxsize = p["qmix_maxsize"]
    reps = p["qmix_reps"]

    def work():
        def body():
            q = env.Queue(maxsize)
            counts = {"n": 0}
            counts_lock = threading.Lock()

            def producer(base):
                for i in range(items):
                    q.put(base + i)

            def consumer():
                for _ in range(items):
                    q.get()
                    with counts_lock:
                        counts["n"] += 1

            native = [threading.Thread(target=producer, args=(1000000,)),
                      threading.Thread(target=consumer)]
            for t in native:
                t.daemon = True
                t.start()
            hp = env.spawn(producer, 0)
            hc = env.spawn(consumer)
            env.joinall([hp, hc])
            for t in native:
                t.join(30)
                if t.is_alive():
                    raise RuntimeError("native thread hung in mixed queue bench")
            if counts["n"] != 2 * items:
                raise RuntimeError("consumed %d != expected %d"
                                   % (counts["n"], 2 * items))
            return counts["n"]
        return env.run(body)

    stats = measure(work, reps)
    return {"items": 2 * items, "maxsize": maxsize, "items_per_sec": stats}


def bench_tpool(env, p):
    calls = p["tpool_calls"]
    reps = p["tpool_reps"]

    def add(a, b):
        return a + b

    def work():
        def body():
            for i in range(calls):
                env.tpool_execute(add, i, 1)
            return calls
        return env.run(body)

    try:
        stats = measure(work, reps)
    finally:
        env.tpool_shutdown()
    med = stats["per_sec_median"]
    return {
        "calls": calls,
        "calls_per_sec": stats,
        "mean_latency_us": round(1e6 / med, 2) if med else None,
    }


def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def bench_echo(env, p):
    """In-process TCP echo server + concurrent client greenthreads, same
    framework for both.  Reports requests/sec and p50/p99 latency."""
    msg = b"x" * p["echo_msg"]
    mlen = len(msg)
    reps = p["echo_reps"]
    results = {}

    for conc, rounds in p["echo_specs"]:
        spec_key = "c%d_r%d" % (conc, rounds)
        spec_samples_rps = []
        last_lat = None
        for _rep in range(reps):
            out = _echo_once(env, msg, mlen, conc, rounds)
            spec_samples_rps.append(out["rps"])
            last_lat = out  # keep latency from last rep
        spec_samples_rps.sort()
        results[spec_key] = {
            "concurrency": conc,
            "roundtrips_each": rounds,
            "total_requests": conc * rounds,
            "requests_per_sec_median": median(spec_samples_rps),
            "requests_per_sec_min": min(spec_samples_rps),
            "requests_per_sec_max": max(spec_samples_rps),
            "p50_ms": last_lat["p50_ms"],
            "p99_ms": last_lat["p99_ms"],
            "reps": reps,
        }
    return results


def _echo_once(env, msg, mlen, conc, rounds):
    """One echo run at a given concurrency; returns rps + latency percentiles."""
    state = {}

    def body():
        srv = env.green_socket()
        srv.setsockopt(_stdsocket.SOL_SOCKET, _stdsocket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(min(4096, conc + 128))
        addr = srv.getsockname()
        handlers = []

        def handle(conn):
            try:
                while True:
                    data = conn.recv(mlen)
                    if not data:
                        break
                    # echo exactly what we got (may be a short read)
                    conn.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        def acceptor():
            # Accept exactly `conc` connections then exit, so no greenthread is
            # ever left blocked in accept() on a fd we are about to close+reuse
            # across reps (that collision deadlocks filament's poller).
            try:
                for _ in range(conc):
                    conn, _ = srv.accept()
                    handlers.append(env.spawn(handle, conn))
            except Exception as e:
                # If accept() fails (e.g. EMFILE at high concurrency under a low
                # RLIMIT_NOFILE), record it and tear down the listener. Closing
                # the listen socket RSTs any backlog connections, so clients
                # blocked in recv() wake with an error instead of the whole run
                # deadlocking in joinall(clients) below.
                state.setdefault("accept_error", "%s: %s" % (type(e).__name__, e))
                try:
                    srv.close()
                except Exception:
                    pass

        acc = env.spawn(acceptor)
        env.sleep(0)  # let acceptor reach accept()

        latencies = []

        def client():
            try:
                s = env.green_socket()
                s.connect(addr)
                for _ in range(rounds):
                    t0 = perf()
                    s.sendall(msg)
                    need = mlen
                    while need > 0:
                        chunk = s.recv(need)
                        if not chunk:
                            break
                        need -= len(chunk)
                    latencies.append(perf() - t0)
                s.close()
            except Exception as e:
                # A reset (from the acceptor tearing down on fd exhaustion) or a
                # socket() EMFILE surfaces here; record it so joinall() can
                # complete and _echo_once reports a real error, not a hang.
                state.setdefault("client_error", "%s: %s" % (type(e).__name__, e))

        t0 = perf()
        clients = [env.spawn(client) for _ in range(conc)]
        env.joinall(clients)
        wall = perf() - t0

        # drain server side cleanly: acceptor has taken all conns, handlers see
        # EOF now that clients closed.
        env.joinall([acc])
        env.joinall(handlers)
        try:
            srv.close()
        except Exception:
            pass

        # A failed acceptor means the run is structurally invalid (not a real
        # throughput measurement). Surface it as an error rather than reporting
        # a bogus partial number -- the driver then records "error", not the
        # 240s "deadlock" this used to produce under fd exhaustion.
        if state.get("accept_error"):
            raise RuntimeError(
                "echo acceptor failed (fd exhaustion at conc=%d?): %s"
                % (conc, state["accept_error"]))

        latencies.sort()
        state["rps"] = (conc * rounds) / wall if wall > 0 else 0.0
        state["p50_ms"] = round(_percentile(latencies, 50) * 1000, 3) if latencies else None
        state["p99_ms"] = round(_percentile(latencies, 99) * 1000, 3) if latencies else None

    env.run(body)
    return state


# ---------------------------------------------------------------------------
# #137 logging-in-threadpool
#
# Monkey-patch everything, then log heavily from real OS-thread pool workers
# while greenthreads spin in the hub.  This is the classic gevent issue #137:
# a real thread that acquires a monkey-patched (green) logging lock / touches
# green threading state deadlocks the hub.  A hung hub cannot run a Python
# SIGALRM handler, so we do NOT try to self-time-out here -- the driver runs
# this worker under a hard subprocess timeout and records "deadlock" if we
# never print a result.  Modes:
#   naive       -- straightforward tpool + logging (the broken path)
#   workaround  -- gevent only: get_hub().threadpool + native logging locks +
#                  logThreads off (the "documented" mitigations); shows whether
#                  they actually save it.
# ---------------------------------------------------------------------------
def bench_logging137(framework, p, mode="naive"):
    import logging

    n_workers = p["log_workers"]
    n_msgs = p["log_msgs"]
    hub_gt = p["log_hub_greenthreads"]

    def make_logger(native_lock=None):
        logger = logging.getLogger("bench137")
        for h in list(logger.handlers):
            logger.removeHandler(h)
        devnull = open(os.devnull, "w")
        handler = logging.StreamHandler(devnull)
        handler.setFormatter(logging.Formatter("%(threadName)s %(message)s"))
        if native_lock is not None:
            handler.lock = native_lock()
            logging._lock = native_lock()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        return logger

    def run_attempt(logger, spawn_workers, sleep_fn, spawn_gt, label):
        done = {"stop": False}

        def hub_spinner():
            while not done["stop"]:
                sleep_fn(0)

        # Heartbeat so the driver can tell "slow" from "wedged".  It is emitted
        # from inside the logging loop deliberately: that is the thing that
        # deadlocks under gevent/eventlet, so a wedged run goes silent while a
        # merely slow one (macOS is ~30-50x slower here than Linux) keeps
        # reporting and is allowed to finish.  stderr, and prefixed, so it
        # never collides with the RESULT_JSON line on stdout.
        beat_every = max(1, n_msgs // 10)

        def worker(wid):
            for i in range(n_msgs):
                logger.debug("worker %d msg %d payload %s", wid, i, "abc" * 3)
                if (i + 1) % beat_every == 0:
                    sys.stderr.write("HEARTBEAT %d %d\n" % (wid, i + 1))
                    sys.stderr.flush()
            return n_msgs

        gts = [spawn_gt(hub_spinner) for _ in range(hub_gt)]
        t0 = perf()
        total = spawn_workers(worker, n_workers)   # may deadlock -> killed by driver
        dt = perf() - t0
        done["stop"] = True
        for gt in gts:
            try:
                if hasattr(gt, "kill"):
                    gt.kill()
            except Exception:
                pass
        mps = total / dt if dt > 0 else 0.0
        return {"status": "ok", "msgs_per_sec": round(mps, 1),
                "error": None, "label": label, "mode": mode}

    # ------------------------------------------------------------------
    if framework == "filament":
        import filament
        import filament.patcher as patcher
        import filament.tpool as ftpool
        patcher.patch_all()
        logger = make_logger()

        def spawn_workers(worker_fn, n):
            handles = [filament.spawn(lambda w=w: ftpool.execute(worker_fn, w))
                       for w in range(n)]
            filament.joinall(handles)
            return n_msgs * n

        res = run_attempt(logger, spawn_workers, filament.sleep, filament.spawn,
                          "filament.tpool")
        try:
            ftpool.shutdown()
        except Exception:
            pass
        return res

    if framework == "gevent":
        from gevent import monkey
        monkey.patch_all()
        import gevent
        import gevent.threadpool

        if mode == "workaround":
            # "documented" mitigations: hub threadpool + native logging locks +
            # no thread/process fields in records.
            logging.logThreads = False
            logging.logMultiprocessing = False
            logging.logProcesses = False
            native = monkey.get_original("threading", "RLock")
            logger = make_logger(native_lock=native)
            pool = gevent.get_hub().threadpool
            label = "gevent.get_hub().threadpool + native locks"
        else:
            logger = make_logger()
            pool = gevent.threadpool.ThreadPool(n_workers + 1)
            label = "gevent.threadpool.ThreadPool"

        def spawn_workers(worker_fn, n):
            ars = [pool.spawn(worker_fn, w) for w in range(n)]
            for ar in ars:
                ar.get()
            return n_msgs * n

        res = run_attempt(logger, spawn_workers, gevent.sleep, gevent.spawn, label)
        try:
            if mode != "workaround":
                pool.kill()
        except Exception:
            pass
        return res

    if framework == "eventlet":
        import eventlet
        eventlet.monkey_patch()
        import eventlet.tpool as etpool
        logger = make_logger()

        def spawn_workers(worker_fn, n):
            gts = [eventlet.spawn(etpool.execute, worker_fn, w) for w in range(n)]
            for gt in gts:
                gt.wait()
            return n_msgs * n

        return run_attempt(logger, spawn_workers, eventlet.sleep, eventlet.spawn,
                           "eventlet.tpool")

    raise ValueError("unknown framework %r" % framework)


# ===========================================================================
# Dispatch
# ===========================================================================
NON_ENV = {"logging137"}


def main():
    framework = sys.argv[1]
    benchmark = sys.argv[2]
    params = dict(DEFAULTS)
    if "--params" in sys.argv:
        raw = sys.argv[sys.argv.index("--params") + 1]
        params.update(json.loads(raw))

    import greenlet
    envelope = {
        "framework": framework,
        "benchmark": benchmark,
        "python": ".".join(str(x) for x in sys.version_info[:3]),
        "greenlet": getattr(greenlet, "__version__", "?"),
        "status": "ok",
        "error": None,
        "lib_version": None,
        "result": None,
    }
    log_mode = "naive"
    if "--log-mode" in sys.argv:
        log_mode = sys.argv[sys.argv.index("--log-mode") + 1]
    envelope["log_mode"] = log_mode

    try:
        if benchmark in NON_ENV:
            envelope["result"] = bench_logging137(framework, params, log_mode)
        else:
            env = make_env(framework)
            envelope["lib_version"] = env.lib_version
            fn = {
                "spawn": bench_spawn,
                "ctxswitch": bench_ctxswitch,
                "semaphore": bench_semaphore,
                "queue": bench_queue,
                "queue_mixed": bench_queue_mixed,
                "tpool": bench_tpool,
                "echo": bench_echo,
            }[benchmark]
            envelope["result"] = fn(env, params)
        if envelope["lib_version"] is None:
            try:
                envelope["lib_version"] = make_env(framework).lib_version
            except Exception:
                pass
    except Exception as e:
        envelope["status"] = "error"
        envelope["error"] = "%s: %s" % (type(e).__name__, e)
        traceback.print_exc()

    sys.stdout.write("RESULT_JSON:" + json.dumps(envelope) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
