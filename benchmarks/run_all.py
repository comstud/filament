#!/usr/bin/env python
"""Driver for the filament vs gevent vs eventlet benchmark suite.

Runs every (framework, benchmark) pair in a FRESH subprocess (so gevent /
eventlet monkey-patching and hub state can never cross-contaminate), collects
the JSON each worker prints, and writes:

    benchmarks/results/<pyver>.json    raw numbers for one interpreter
    benchmarks/RESULTS.md              aggregated markdown report (all versions)

Deadlock handling: the #137 logging benchmark can hang gevent/eventlet forever.
Each worker runs under a hard subprocess timeout in its own process group; on
timeout we SIGKILL the group and record status "deadlock".

Usage:
    python benchmarks/run_all.py [--python PATH] [--benchmarks a,b,c]
                                 [--scale full|small] [--report-only]

--python       interpreter to run workers with (default: this interpreter).
--benchmarks   comma list to filter (spawn,ctxswitch,semaphore,queue,tpool,
               echo,logging137). Default: all.
--scale        full (default) or small (quick smoke sizes).
--report-only  skip running; just rebuild RESULTS.md from existing results/*.json.
--timeout-scale  multiply all per-benchmark timeouts (see TIMEOUTS below); use
               this to check whether a reported "deadlock" is really a hang.
"""
from __future__ import print_function

import argparse
import atexit
import glob
import json
import os
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "worker.py")
RESULTS_DIR = os.path.join(HERE, "results")
REPORT = os.path.join(HERE, "RESULTS.md")

FRAMEWORKS = ["filament", "gevent", "eventlet"]
ALL_BENCHMARKS = ["spawn", "ctxswitch", "semaphore", "queue", "queue_mixed",
                  "tpool", "echo", "logging137"]

# Per-benchmark subprocess timeout (seconds).
#
# NOTE: exceeding one of these is recorded as status "deadlock", but nothing
# actually detects a deadlock -- it only means "printed no result in time".  On
# a new or slow platform a cell can be merely slow and get labelled a hang, so
# before believing a "deadlock" raise the limit (--timeout-scale) and see
# whether it completes.  logging137's 45s is the tightest budget in the suite
# and it runs last, right after echo@1000 -- which on some systems is still
# tearing down thousands of fiber stacks when the next subprocess starts.
TIMEOUTS = {
    "spawn": 240, "ctxswitch": 180, "semaphore": 120, "queue": 120,
    "queue_mixed": 60, "tpool": 120, "echo": 240, "logging137": 45,
}


def _timeout_for(bench):
    """Per-benchmark timeout, scaled by --timeout-scale / FIL_BENCH_TIMEOUT_SCALE."""
    try:
        scale = float(os.environ.get("FIL_BENCH_TIMEOUT_SCALE", "1") or 1)
    except ValueError:
        scale = 1.0
    return max(1, int(TIMEOUTS[bench] * scale))

SMALL_PARAMS = {
    "spawn_k": 5000, "spawn_reps": 3,
    "ctx_greenthreads": 20, "ctx_iters": 2000, "ctx_reps": 3,
    "sem_uncontended": 100000, "sem_contended_gt": 20, "sem_contended_ops": 1000,
    "sem_reps": 3, "queue_items": 20000, "queue_reps": 3,
    "qmix_items": 5000, "qmix_reps": 2,
    "tpool_calls": 500, "tpool_reps": 3,
    "echo_reps": 2, "echo_specs": [[100, 30], [500, 10]],
    "log_workers": 4, "log_msgs": 3000, "log_hub_greenthreads": 8,
}
FULL_PARAMS = {}  # worker DEFAULTS are the full sizes


def detect_pyver(python):
    out = subprocess.check_output(
        [python, "-c", "import sys;print('.'.join(str(x) for x in sys.version_info[:3]))"])
    return out.decode().strip()


def _norm_arch(machine):
    m = (machine or "").lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    return m or "unknown"


def detect_freethreaded(python):
    """(is a PEP 703 free-threaded build, is the GIL actually enabled).

    Both matter and they are not the same question: a free-threaded build still
    runs with the GIL on if something re-enables it (PYTHON_GIL=1, or an
    extension that has not declared Py_MOD_GIL_NOT_USED), and a table recorded
    that way measures the stock runtime under a free-threaded label."""
    code = ("import sys, sysconfig;"
            "print('%d %d' % ("
            "bool(sysconfig.get_config_var('Py_GIL_DISABLED')),"
            "getattr(sys, '_is_gil_enabled', lambda: True)()))")
    try:
        out = subprocess.check_output([python, "-c", code]).decode().split()
        return bool(int(out[0])), bool(int(out[1]))
    except Exception:
        return False, True


def detect_arch(python):
    """Architecture of the *target* interpreter, normalized to amd64/arm64.

    Results are partitioned by arch (results/<arch>/<pyver>.json) so an amd64
    run never clobbers the aarch64 numbers (and vice versa)."""
    try:
        out = subprocess.check_output(
            [python, "-c", "import platform;print(platform.machine())"])
        return _norm_arch(out.decode().strip())
    except Exception:
        import platform
        return _norm_arch(platform.machine())


# Workers are put in their own session (preexec_fn=os.setsid) so a wedged one
# can be killpg'd as a group.  The flip side is that they are DETACHED from this
# process group, so a Ctrl-C / SIGTERM / wrapper timeout aimed at the driver
# never reaches them -- and a deadlocked gevent or eventlet worker then spins at
# 100% of a core indefinitely (#137 leaves up to three per interpreter).  Track
# the live group and tear it down however this process exits.
_LIVE_PGIDS = set()


def _reap_children(*_args):
    for pgid in list(_LIVE_PGIDS):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except Exception:
            pass
    _LIVE_PGIDS.clear()


def _install_cleanup():
    atexit.register(_reap_children)
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            prev = signal.getsignal(signum)
        except Exception:
            continue

        def _handler(sn, frame, _prev=prev):
            _reap_children()
            # Restore the default action and re-raise so the driver still dies
            # with the right status instead of swallowing the signal.
            try:
                signal.signal(sn, signal.SIG_DFL)
            except Exception:
                pass
            os.kill(os.getpid(), sn)

        try:
            signal.signal(signum, _handler)
        except Exception:
            pass


_install_cleanup()


def run_worker(python, framework, benchmark, params, timeout, extra=None):
    cmd = [python, WORKER, framework, benchmark, "--params", json.dumps(params)]
    if extra:
        cmd += extra
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if hasattr(os, "setsid"):
        kwargs["preexec_fn"] = os.setsid
    p = subprocess.Popen(cmd, **kwargs)
    try:
        _LIVE_PGIDS.add(os.getpgid(p.pid))
    except Exception:
        _LIVE_PGIDS.add(p.pid)

    # The timeout is on IDLENESS, not total runtime.  A deadlocked worker goes
    # completely silent (that is the whole point of #137 -- gevent/eventlet wedge
    # between the hub and the logging lock), while a worker that is merely slow
    # keeps emitting HEARTBEAT lines and is allowed to run to completion.  That
    # matters on platforms where a benchmark is far slower than on Linux: macOS
    # runs #137 at roughly 3k msg/s against Linux's ~175k, so the full 80k-message
    # workload needs ~27s where a fixed 30s budget was a coin flip.  Benchmarks
    # that emit nothing are unaffected -- for them idle time == total time, which
    # is exactly the old behaviour.
    out_buf, err_buf = [], []
    last_activity = [time.time()]

    def _drain(stream, sink):
        try:
            for line in iter(stream.readline, b""):
                sink.append(line)
                last_activity[0] = time.time()
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    readers = [threading.Thread(target=_drain, args=(s, b))
               for s, b in ((p.stdout, out_buf), (p.stderr, err_buf))]
    for t in readers:
        t.daemon = True
        t.start()

    timed_out = False
    while True:
        if p.poll() is not None:
            break
        if time.time() - last_activity[0] > timeout:
            timed_out = True
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            break
        time.sleep(0.2)

    for t in readers:
        t.join(timeout=10)
    try:
        p.wait(timeout=10)
    except Exception:
        pass
    # Whatever happened, this group is no longer ours to reap.
    try:
        _LIVE_PGIDS.discard(os.getpgid(p.pid))
    except Exception:
        _LIVE_PGIDS.discard(p.pid)

    out = b"".join(out_buf).decode("utf-8", "replace")
    err = b"".join(err_buf).decode("utf-8", "replace")
    # Heartbeats are progress signalling, not diagnostics; keep them out of the
    # stderr tail that gets reported on failure.
    err = "\n".join(l for l in err.splitlines()
                    if not l.startswith("HEARTBEAT "))

    envelope = None
    for line in out.splitlines():
        if line.startswith("RESULT_JSON:"):
            try:
                envelope = json.loads(line[len("RESULT_JSON:"):])
            except Exception:
                pass
    if envelope is None:
        # No result printed -> deadlock (timeout), a fatal signal, or a
        # traceback.  Distinguish the signal: "eventlet segfaults here" and
        # "eventlet raised" are different claims, and lumping both under
        # "crash" loses the stronger one.  Python reports a signal death as a
        # negative returncode.
        rc = p.returncode
        if timed_out:
            status, why = "deadlock", "timeout after %ss" % timeout
        elif rc is not None and rc < 0:
            signame = _signame(-rc)
            status = "segfault" if -rc == signal.SIGSEGV else "signal"
            why = "killed by %s" % signame
        else:
            status, why = "crash", None
        if why is None or status != "deadlock":
            # Keep enough stderr to get past a library's import-time banner --
            # eventlet prints an eight-line deprecation notice that pushed the
            # actual failure out of a four-line tail.
            tail = "\n".join(l for l in err.strip().splitlines() if l.strip())
            tail = "\n".join(tail.splitlines()[-12:])
            why = ((why + "; ") if why else "no result; ") + "stderr tail:\n" + tail
        envelope = {
            "framework": framework, "benchmark": benchmark,
            "status": status, "error": why, "result": None,
        }
    return envelope


def _signame(num):
    for name in dir(signal):
        if name.startswith("SIG") and not name.startswith("SIG_"):
            if getattr(signal, name) == num:
                return "%s (%d)" % (name, num)
    return "signal %d" % num


def run_matrix(python, benchmarks, params, pyver):
    results = {"python": pyver, "worker_params": params, "runs": {}}
    for b in benchmarks:
        results["runs"][b] = {}
        for fw in FRAMEWORKS:
            if b == "logging137":
                # naive attempt for all three
                env = run_worker(python, fw, b, params, _timeout_for(b),
                                 extra=["--log-mode", "naive"])
                results["runs"][b][fw] = env
                _log_line(fw, b + " (naive)", env)
                # extra: gevent "documented workaround" attempt
                if fw == "gevent":
                    wa = run_worker(python, fw, b, params, _timeout_for(b),
                                    extra=["--log-mode", "workaround"])
                    results["runs"][b][fw + ":workaround"] = wa
                    _log_line(fw, b + " (workaround)", wa)
            else:
                env = run_worker(python, fw, b, params, _timeout_for(b))
                results["runs"][b][fw] = env
                _log_line(fw, b, env)
    return results


def _log_line(fw, b, env):
    st = env.get("status")
    extra = ""
    r = env.get("result")
    if st == "ok" and isinstance(r, dict):
        if b.startswith("logging137"):
            rr = r
            extra = "  %s / %s msg/s" % (rr.get("status"), rr.get("msgs_per_sec"))
        elif b == "spawn":
            extra = "  %.0f gt/s" % r["tracked_spawn_per_sec"]["per_sec_median"]
        elif b == "ctxswitch":
            extra = "  %.0f sw/s" % r["switches_per_sec"]["per_sec_median"]
    elif st != "ok":
        extra = "  (%s)" % (env.get("error") or "")[:60]
    print("  [%-8s] %-22s %-9s%s" % (fw, b, st, extra))
    sys.stdout.flush()


# ===========================================================================
# Report generation
# ===========================================================================
def _fmt_num(x):
    if x is None:
        return "-"
    if x >= 1e6:
        return "%.2fM" % (x / 1e6)
    if x >= 1e3:
        return "%.1fk" % (x / 1e3)
    return "%.1f" % x


def _cell(env, extract):
    if env is None:
        return "n/a"
    if env.get("status") != "ok":
        return "**%s**" % env.get("status")
    try:
        return extract(env["result"])
    except Exception:
        return "err"


def _host_describe():
    """CPU model / core count / virtualization, for the Environments table.

    Worth recording: the same 'amd64' label on bare metal and inside a guest
    on that same machine produced tpool numbers 4x apart, because every
    cross-thread wakeup is far more expensive when virtualized.
    """
    model, threads, pairs = "", 0, set()
    phys, core = None, None

    # macOS has no /proc; everything below comes from sysctl instead.  Without
    # this the whole field comes back empty on Darwin.
    if sys.platform == "darwin":
        def _sysctl(key):
            try:
                out = subprocess.check_output(["sysctl", "-n", key],
                                              stderr=open(os.devnull, "w"))
                return out.decode("utf-8", "replace").strip()
            except Exception:
                return ""
        model = _sysctl("machdep.cpu.brand_string")
        phys_n = _sysctl("hw.physicalcpu")
        log_n = _sysctl("hw.logicalcpu")
        if phys_n and log_n and phys_n != log_n:
            cores = "%sc/%st" % (phys_n, log_n)
        elif phys_n:
            cores = "%sc" % phys_n
        else:
            cores = ""
        # kern.hv_vmm_present is 1 inside a VM, 0/absent on bare metal.
        virt = "VM" if _sysctl("kern.hv_vmm_present") == "1" else "bare metal"
        return ", ".join(p for p in (model, cores, virt) if p)

    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.startswith(("model name", "Model")) and not model:
                    model = line.split(":", 1)[1].strip()
                elif line.startswith("processor"):
                    threads += 1
                elif line.startswith("physical id"):
                    phys = line.split(":", 1)[1].strip()
                elif line.startswith("core id"):
                    core = line.split(":", 1)[1].strip()
                    if phys is not None:
                        pairs.add((phys, core))
    except Exception:
        pass
    # "32c/64t" is the useful shape: SMT changes what a core-count means, and
    # the two OS-thread benchmarks care a lot about which sibling they land on.
    if pairs and threads:
        cores = "%dc/%dt" % (len(pairs), threads)
    elif threads:
        cores = "%d cpus" % threads
    else:
        cores = ""
    # the model string usually already spells out the core count
    for suffix in (" %d-Cores" % len(pairs), " %d-Core Processor" % len(pairs)):
        if model.endswith(suffix):
            model = model[:-len(suffix)]
    # Only claim "bare metal" when something actually said so -- an absent
    # detector is not evidence of one.
    virt = ""
    try:
        out = subprocess.check_output(["systemd-detect-virt"],
                                      stderr=open(os.devnull, "w"))
        virt = out.decode("utf-8", "replace").strip()
        if virt == "none":
            virt = "bare metal"
    except Exception:
        if os.path.exists("/.dockerenv"):
            virt = "container"
    parts = [p for p in (model, cores, virt) if p]
    return ", ".join(parts)


def _git_describe():
    # The benchmark host is not always a git checkout -- the usual way to get a
    # tree onto one is `git archive HEAD | ssh host tar -x`, which carries no
    # .git and may land somewhere git is not even installed.  Recording the
    # commit still matters (a table with no provenance is a table you cannot
    # re-run), so let the shipper name it.
    env = os.environ.get("FIL_BENCH_COMMIT")
    if env:
        return env.strip()
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                      cwd=HERE, stderr=open(os.devnull, "w"))
    except Exception:
        return ""
    return out.decode("utf-8", "replace").strip()


def build_report():
    # Results are partitioned by arch: results/<arch>/<pyver>.json. Fall back to
    # any legacy flat files (tagged "unknown") so old layouts still render.
    files = sorted(glob.glob(os.path.join(RESULTS_DIR, "*", "*.json")))
    files += sorted(glob.glob(os.path.join(RESULTS_DIR, "*.json")))
    data = []
    for f in files:
        try:
            with open(f) as fh:
                d = json.load(fh)
        except Exception:
            continue
        parent = os.path.basename(os.path.dirname(f))
        d["_arch"] = parent if parent != os.path.basename(RESULTS_DIR) else "unknown"
        data.append(d)
    # sort arch-major, then python version descending within each arch
    def vk(d):
        return (d["_arch"], tuple(-int(x) for x in d["python"].split(".")))
    data.sort(key=vk)

    lines = []
    lines.append("# filament vs gevent vs eventlet \u2014 benchmark results")
    lines.append("")
    lines.append("Higher is better on every row except the latency rows, where "
                 "lower is better. Cell values other than a number:")
    lines.append("")
    lines.append("| Cell | Meaning |")
    lines.append("|---|---|")
    lines.append("| **error** | the framework raised; the benchmark did not "
                 "complete |")
    lines.append("| **deadlock** | printed nothing for the whole idle timeout "
                 "and was killed |")
    lines.append("| **crash** | exited without printing a result |")
    lines.append("| **segfault** / **signal** | killed by a fatal signal |")
    lines.append("| n/a | not run for this interpreter |")
    lines.append("| \u2020 | the row crosses OS threads, so its absolute value "
                 "moves with thread placement |")
    lines.append("| not available | the library could not be installed on this "
                 "interpreter |")
    lines.append("| echo, driven remotely | server only, driven from a second "
                 "machine by one fixed Go generator; mean of 3 alternated reps |")
    lines.append("| echo, in-process | client and server on one runtime in one "
                 "process (no second host available for that row) |")
    lines.append("")
    lines.append("## Environments")
    lines.append("")
    lines.append("| Arch | Python | greenlet | gevent | eventlet | host | measured |")
    lines.append("|---|---|---|---|---|---|---|")
    for d in data:
        gv = _lib_ver(d, "gevent")
        ev = _lib_ver(d, "eventlet")
        gl = _greenlet_ver(d)
        when = d.get("measured_utc") or "—"
        commit = d.get("commit")
        if commit:
            when = "%s (%s)" % (when, commit)
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" %
                     (_arch_label(d["_arch"]), d["python"], gl, gv, ev,
                      d.get("host") or "—", when))
    lines.append("")

    by_key = dict(((x["_arch"], x["python"]), x) for x in data)
    for d in data:
        lines += _report_for_version(d)
        if d.get("free_threaded"):
            stock = by_key.get((d["_arch"][:-len("-ft")], d["python"]))
            if stock is not None:
                lines += _gil_delta_section(d, stock)

    with open(REPORT, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("wrote", REPORT)


def _arch_label(arch):
    """Display name for a results directory ('amd64-ft' -> 'amd64 free-threaded')."""
    if arch.endswith("-ft"):
        return arch[:-len("-ft")] + " free-threaded"
    return arch


def _greenlet_ver(d):
    for b in d["runs"].values():
        for env in b.values():
            if env.get("greenlet"):
                return env["greenlet"]
    return "?"


def _lib_ver(d, fw):
    for b in d["runs"].values():
        env = b.get(fw)
        if env and env.get("lib_version"):
            return env["lib_version"]
    # maybe it errored everywhere
    for b in d["runs"].values():
        env = b.get(fw)
        if env and env.get("status") != "ok":
            return "not available"
    return "?"


def _get(d, bench, fw):
    return d["runs"].get(bench, {}).get(fw)


# (benchmark, label, metric, extractor) for the numeric throughput rows.  Used
# by the GIL on/off comparison; the main per-version table formats its own so
# it can render "-" and bold failures.
_THROUGHPUT_ROWS = [
    ("spawn", "spawn (tracked, spawn+join)", "greenthreads/s",
     lambda r: r["tracked_spawn_per_sec"]["per_sec_median"]),
    ("spawn", "spawn (fire & forget)", "spawn_n/s",
     lambda r: r["fireforget_spawn_n_per_sec"]["per_sec_median"]),
    ("ctxswitch", "context switch", "switches/s",
     lambda r: r["switches_per_sec"]["per_sec_median"]),
    ("semaphore", "semaphore uncontended", "ops/s",
     lambda r: r["uncontended_ops_per_sec"]["per_sec_median"]),
    ("semaphore", "semaphore contended", "ops/s",
     lambda r: r["contended_ops_per_sec"]["per_sec_median"]),
    ("queue", "queue put/get", "items/s",
     lambda r: r["items_per_sec"]["per_sec_median"]),
    ("queue_mixed", "queue shared green+native threads", "items/s",
     lambda r: r["items_per_sec"]["per_sec_median"]),
    ("tpool", "tpool round-trip", "calls/s",
     lambda r: r["calls_per_sec"]["per_sec_median"]),
    ("logging137", "#137 logging from threadpool", "msgs/s",
     lambda r: r["msgs_per_sec"]),
]

# Benchmarks whose filament side runs work on a second OS thread (io thread or
# real thread pool).  On a many-core host these are the placement-sensitive
# ones -- see the OS-thread caveat -- so a single measurement of one is a draw,
# not a value.
_CROSS_THREAD = frozenset(["queue_mixed", "tpool", "echo", "netecho",
                           "logging137"])


def _num(d, bench, fw, extract):
    env = _get(d, bench, fw)
    if not env or env.get("status") != "ok":
        return None
    try:
        v = extract(env["result"])
    except Exception:
        return None
    return v if isinstance(v, (int, float)) else None


def _gil_delta_section(ft, stock):
    """filament with the GIL off vs the same filament with it on.

    The two runs differ in one thing only -- the interpreter build -- so unlike
    every other cross-table comparison in this document this ratio means
    something.  It is still a SINGLE-scheduler measurement: what it prices is
    the free-threaded interpreter (biased reference counting, no free list
    reuse, deferred reclamation), not filament scaling across cores.

    Which is exactly why the two runs have to be comparable before any ratio is
    printed.  Matching (arch, version) is not enough: an aarch64 free-threaded
    run in a container next to an aarch64 stock run on an Apple host produced
    0.17x and 19.05x cells that measured the two machines, not the GIL.  Same
    host and same benchmark sizes, or no table."""
    same_host = ft.get("host") and ft.get("host") == stock.get("host")
    same_sizes = ft.get("worker_params") == stock.get("worker_params")
    if not (same_host and same_sizes):
        why = []
        if not same_host:
            why.append("they ran on different hosts")
        if not same_sizes:
            why.append("they ran at different benchmark sizes")
        # Tables only: a comparison that cannot honestly be drawn is simply
        # absent.  `why` says which guard failed, for whoever debugs that.
        del why
        return []

    rows = list(_THROUGHPUT_ROWS)
    # Same preference as the per-version table: the remotely driven echo is a
    # server measurement, the in-process one is not.
    if _netecho_specs(ft):
        for key, conc in _netecho_specs(ft):
            rows.append(("netecho", "echo, driven remotely @ conc %d" % conc,
                         "req/s",
                         (lambda k: (lambda r: r[k]["requests_per_sec_mean"]))(key)))
    else:
        for key, conc in _echo_specs(ft):
            rows.append(("echo", "echo, in-process @ conc %d" % conc, "req/s",
                         (lambda k: (lambda r: r[k]["requests_per_sec_median"]))(key)))

    L = ["### filament: GIL off vs GIL on (same host, Python %s both sides)"
         % ft["python"], ""]
    L.append("| Benchmark | Metric | GIL off | GIL on | off/on |")
    L.append("|---|---|---|---|---|")
    for bench, label, metric, extract in rows:
        a = _num(ft, bench, "filament", extract)
        b = _num(stock, bench, "filament", extract)
        if a is None and b is None:
            continue
        if bench in _CROSS_THREAD:
            label += " †"
        ratio = "%.2fx" % (a / b) if (a and b) else "—"
        L.append("| %s | %s | %s | %s | %s |" %
                 (label, metric, _fmt_num(a) if a else "**n/a**",
                  _fmt_num(b) if b else "**n/a**", ratio))
    L.append("")
    return L


def _report_for_version(d):
    L = []
    L.append("## %s · Python %s" % (_arch_label(d.get("_arch", "?")), d["python"]))
    L.append("")
    L.append("| Benchmark | Metric | filament | gevent | eventlet |")
    L.append("|---|---|---|---|---|")

    def row(bench_key, label, metric, extract):
        cells = [_cell(_get(d, bench_key, fw), extract) for fw in FRAMEWORKS]
        L.append("| %s | %s | %s | %s | %s |" % (label, metric,
                 cells[0], cells[1], cells[2]))

    row("spawn", "spawn (tracked, spawn+join)", "greenthreads/s",
        lambda r: _fmt_num(r["tracked_spawn_per_sec"]["per_sec_median"]))
    row("spawn", "spawn (fire & forget)", "spawn_n/s",
        lambda r: _fmt_num(r["fireforget_spawn_n_per_sec"]["per_sec_median"])
        if r.get("fireforget_spawn_n_per_sec") else "-")
    row("ctxswitch", "context switch", "switches/s",
        lambda r: _fmt_num(r["switches_per_sec"]["per_sec_median"]))
    row("semaphore", "semaphore uncontended", "ops/s",
        lambda r: _fmt_num(r["uncontended_ops_per_sec"]["per_sec_median"]))
    row("semaphore", "semaphore contended", "ops/s",
        lambda r: _fmt_num(r["contended_ops_per_sec"]["per_sec_median"]))
    row("queue", "queue put/get", "items/s",
        lambda r: _fmt_num(r["items_per_sec"]["per_sec_median"]))
    row("queue_mixed", "queue shared green+native threads", "items/s",
        lambda r: _fmt_num(r["items_per_sec"]["per_sec_median"]))
    row("tpool", "tpool round-trip", "calls/s",
        lambda r: _fmt_num(r["calls_per_sec"]["per_sec_median"]))
    row("tpool", "tpool round-trip", "mean latency",
        lambda r: "%s us" % r["mean_latency_us"])
    # Echo.  Prefer netecho when this interpreter has it: the in-process echo
    # runs the client and the server on one runtime, so it cannot tell a fast
    # server from a fast client, while netecho drives the server from another
    # machine with one fixed Go generator.  Fall back to the in-process rows
    # where netecho could not run (it needs a second host).
    net = d["runs"].get("netecho")
    if net and any((net.get(fw) or {}).get("status") == "ok" for fw in FRAMEWORKS):
        for key, conc in _netecho_specs(d):
            row("netecho", "echo, driven remotely @ conc %d" % conc, "req/s",
                (lambda k: (lambda r: _fmt_num(r[k]["requests_per_sec_mean"])))(key))
            row("netecho", "echo, driven remotely @ conc %d" % conc, "p50/p99 ms",
                (lambda k: (lambda r: "%s / %s" % (r[k]["p50_ms"], r[k]["p99_ms"])))(key))
    else:
        for key, conc in _echo_specs(d):
            row("echo", "echo, in-process @ conc %d" % conc, "req/s",
                (lambda k: (lambda r: _fmt_num(r[k]["requests_per_sec_median"])))(key))
            row("echo", "echo, in-process @ conc %d" % conc, "p50/p99 ms",
                (lambda k: (lambda r: "%s / %s" % (r[k]["p50_ms"], r[k]["p99_ms"])))(key))
    L.append("")

    # logging137 detail
    L += _logging_detail(d)
    return L


def _netecho_specs(d):
    for fw in FRAMEWORKS:
        env = _get(d, "netecho", fw)
        if env and env.get("status") == "ok" and isinstance(env.get("result"), dict):
            return [(k, v["concurrency"]) for k, v in
                    sorted(env["result"].items(), key=lambda kv: kv[1]["concurrency"])]
    return []


def _echo_specs(d):
    for fw in FRAMEWORKS:
        env = _get(d, "echo", fw)
        if env and env.get("status") == "ok" and isinstance(env.get("result"), dict):
            out = []
            for k, v in sorted(env["result"].items(),
                               key=lambda kv: kv[1]["concurrency"]):
                out.append((k, v["concurrency"]))
            return out
    return []


def _logging_detail(d):
    L = ["### #137 logging-from-threadpool (monkey-patched)", ""]
    L.append("| Framework | Path | Result | throughput |")
    L.append("|---|---|---|---|")
    order = [("filament", "filament"),
             ("gevent", "gevent"),
             ("gevent", "gevent:workaround"),
             ("eventlet", "eventlet")]
    for fw, key in order:
        env = _get(d, "logging137", key)
        if env is None:
            continue
        st = env.get("status")
        r = env.get("result")
        if st == "ok" and isinstance(r, dict):
            label = r.get("label", "")
            inner = r.get("status")
            mps = r.get("msgs_per_sec")
            verdict = "OK — completed" if inner == "ok" else inner
            thr = "%s msg/s" % _fmt_num(mps) if mps else "-"
        else:
            label = "workaround" if "workaround" in key else "naive"
            verdict = "**%s**" % st          # matches the legend's wording
            thr = "-"
        L.append("| %s | %s | %s | %s |" % (fw, label, verdict, thr))
    L.append("")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--benchmarks", default="")
    ap.add_argument("--scale", default="full", choices=["full", "small"])
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--timeout-scale", type=float, default=None,
                    help="multiply every per-benchmark timeout by this factor. "
                         "A 'deadlock' only means the worker printed no result "
                         "in time -- raise this to tell a genuine hang from a "
                         "cell that is merely slow on a new/loaded platform.")
    ap.add_argument("--arch", default="",
                    help="override the results arch subdir (default: auto-detect "
                         "the target interpreter's machine, e.g. amd64/arm64)")
    args = ap.parse_args()

    if args.timeout_scale:
        os.environ["FIL_BENCH_TIMEOUT_SCALE"] = str(args.timeout_scale)

    if not os.path.isdir(RESULTS_DIR):
        os.makedirs(RESULTS_DIR)

    if args.report_only:
        build_report()
        return

    benchmarks = ALL_BENCHMARKS
    if args.benchmarks:
        benchmarks = [b.strip() for b in args.benchmarks.split(",") if b.strip()]

    params = dict(FULL_PARAMS if args.scale == "full" else SMALL_PARAMS)
    pyver = detect_pyver(args.python)
    print("== Benchmarking with %s (Python %s), scale=%s ==" %
          (args.python, pyver, args.scale))
    print("benchmarks:", ", ".join(benchmarks))
    print()

    # A free-threaded build gets its own results directory (amd64-ft) rather
    # than its own version string: the file name is the version, and 3.14.6t
    # would collide with stock 3.14.6 on one axis while breaking the numeric
    # version sort on the other.
    freethreaded, gil_on = detect_freethreaded(args.python)
    arch = args.arch or (detect_arch(args.python) + ("-ft" if freethreaded else ""))
    results = run_matrix(args.python, benchmarks, params, pyver)
    archdir = os.path.join(RESULTS_DIR, arch)
    if not os.path.isdir(archdir):
        os.makedirs(archdir)
    outfile = os.path.join(archdir, "%s.json" % pyver)
    if set(benchmarks) != set(ALL_BENCHMARKS) and os.path.exists(outfile):
        # Partial run: merge into the existing full matrix instead of
        # clobbering the other benchmarks' recorded results.
        with open(outfile) as fh:
            merged = json.load(fh)
        merged.setdefault("runs", {}).update(results["runs"])
        results = merged
    # Stamp when and from what these numbers came.  Tables from different runs
    # sit side by side in one report, and a stale one is otherwise
    # indistinguishable from a fresh one.
    results["measured_utc"] = time.strftime("%Y-%m-%d", time.gmtime())
    results["commit"] = _git_describe()
    results["host"] = _host_describe()
    results["free_threaded"] = freethreaded
    results["gil_enabled"] = gil_on
    with open(outfile, "w") as fh:
        json.dump(results, fh, indent=2)
    print("\nwrote", outfile)

    build_report()


if __name__ == "__main__":
    main()
