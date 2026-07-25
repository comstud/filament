"""Cross-thread ping-pong microbenchmark.

Directly measures the cost of filament's cross-thread wakeup chain:

  greenthread (main scheduler thread)  <->  plain OS thread

Round trip = 2 cross-thread signals:
  - OS thread release()s the semaphore the greenthread is parked on
    (GIL-holding foreign-thread signal -> enqueue deferred switch onto the
    greenthread's home scheduler + pthread_cond_signal)
  - greenthread release()s the semaphore the OS thread is parked on
    (OS-thread waiter: fil_waiter_signal drops the GIL around the condvar
    signal -- the #137-critical scheduling point)

Usage:
    python benchmarks/xping.py filament [rounds]
    python benchmarks/xping.py gevent   [rounds]

Prints one RESULT_JSON line: rounds/sec median of 5 reps.
"""
from __future__ import print_function

import json
import sys
import threading
import time

perf = getattr(time, "perf_counter", time.time)

ROUNDS = 20000
REPS = 5


def bench_filament(rounds):
    import filament
    from filament import locking

    sem_gt = locking.Semaphore(0)   # greenthread parks here
    sem_os = locking.Semaphore(0)   # OS thread parks here
    stop = [False]

    def os_thread_body():
        while True:
            sem_os.acquire()
            if stop[0]:
                return
            sem_gt.release()

    thr = threading.Thread(target=os_thread_body)
    thr.daemon = True
    thr.start()

    def gt_body():
        for _ in range(rounds):
            sem_os.release()
            sem_gt.acquire()

    times = []
    for _ in range(REPS):
        t0 = perf()
        f = filament.spawn(gt_body)
        f.wait()
        times.append(perf() - t0)

    stop[0] = True
    sem_os.release()
    thr.join()
    return times


def bench_gevent(rounds):
    """gevent's supported cross-thread wakeup: hub async watcher.

    greenthread parks on gevent.event.Event; the OS thread sets it via
    hub.loop.async_ (the only documented thread-safe wakeup) and parks on a
    plain threading.Event which the greenthread sets (plain condvar, safe).
    """
    import gevent
    from gevent.event import Event as GEvent

    hub = gevent.get_hub()
    g_ev = GEvent()
    watcher = hub.loop.async_()

    def _wake():
        g_ev.set()
    watcher.start(_wake)

    os_ev = threading.Event()
    stop = [False]

    def os_thread_body():
        while True:
            os_ev.wait()
            os_ev.clear()
            if stop[0]:
                return
            watcher.send()

    thr = threading.Thread(target=os_thread_body)
    thr.daemon = True
    thr.start()

    def gt_body():
        for _ in range(rounds):
            os_ev.set()
            g_ev.wait()
            g_ev.clear()

    times = []
    for _ in range(REPS):
        t0 = perf()
        g = gevent.spawn(gt_body)
        g.join()
        times.append(perf() - t0)

    stop[0] = True
    os_ev.set()
    thr.join()
    return times


def bench_gevent_threadpool(rounds):
    """Reference: gevent cross-thread round trip via its own threadpool
    (AsyncResult under the hood)."""
    import gevent
    from gevent.threadpool import ThreadPool

    pool = ThreadPool(1)

    def noop():
        return None

    times = []
    for _ in range(REPS):
        t0 = perf()
        for _ in range(rounds):
            pool.apply(noop)
        times.append(perf() - t0)
    pool.kill()
    return times


def main():
    which = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else ROUNDS

    if which == "filament":
        times = bench_filament(rounds)
    elif which == "gevent":
        times = bench_gevent(rounds)
    elif which == "gevent-tp":
        times = bench_gevent_threadpool(rounds)
    else:
        raise SystemExit("unknown framework: %s" % which)

    times.sort()
    med = times[len(times) // 2]
    print("RESULT_JSON:" + json.dumps({
        "framework": which,
        "rounds": rounds,
        "median_s": med,
        "rps": rounds / med,
        "all_rps": [rounds / t for t in times],
    }))


if __name__ == "__main__":
    main()
