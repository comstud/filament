========
Filament
========

Filament is a greenlet-based cooperative concurrency library for Python — an
efficient alternative to `gevent <http://www.gevent.org/>`_ and
`eventlet <https://eventlet.readthedocs.io/>`_ built around a small C core.
It gives you lightweight "greenthreads" that yield to a scheduler on I/O and
synchronization instead of blocking OS threads, plus cooperative drop-in
replacements for the standard library (``socket``, ``ssl``, ``select``,
``time``, ``os``, ``subprocess``, ``threading``, ``queue``) and near drop-in
compatibility shims for both ``gevent`` and ``eventlet``.

It runs on **CPython 2.7 and 3.8–3.13** (the same source, one set of C
extensions), and its whole reason for existing is that it does **not** have the
cross-thread greenlet-switch bug that still bites gevent and eventlet today
(see `Why filament exists`_).

.. contents::
   :local:


Why filament exists
===================

greenlet binds every greenlet to the OS thread that first switched into it;
switching into it from another thread raises
``greenlet.error: cannot switch to a different thread``. gevent and eventlet
each run a per-thread "hub", but their synchronization primitives can end up
trying to wake a greenlet that lives on a *different* thread. The classic
trigger is the standard ``logging`` module: it holds a module-level mutex, and
once that mutex has been monkey-patched into a green lock, logging from inside
a real OS-thread pool while the hub runs greenthreads on the main thread
deadlocks (or crashes with the error above).

This was reported against eventlet more than a decade ago as **Bitbucket issue
#137** ("Use of threading[ locks]… causes deadlock"). It was serious enough
that OpenStack Nova cites it by URL in commit ``e4b0d8e944`` (closing Launchpad
bug #1252409 — *"the use of logging in this method hit this bug in eventlet …
causing nova-compute to hang"*) and worked around it again for guestfs in
``7c30da1384``. The GitHub-era restatement is eventlet issue #432
(*"Semaphore … does not work across different hubs in different pthreads"*).
The author's own first attempt to fix it in eventlet was commit
``f5a7eaacf78e`` (per-thread waiter dicts + a ``threading.Condition`` to signal
across threads); the fully-correct fix proved too expensive, which is what
motivated writing filament.

Filament's answer is structural:

* **One scheduler per OS thread**, stored in thread-specific data. A
  scheduler is itself a greenlet running an event loop with the GIL released.
* **Waiters bind (scheduler, greenlet) together** at the moment they wait.
* **Signalling never switches a greenlet across threads.** Waking a waiter only
  *enqueues* the switch onto that greenlet's home scheduler and pokes its
  condition variable; the actual ``greenlet.switch()`` happens later, on the
  owning thread, from that scheduler's own loop.

So a wakeup originating in the I/O thread, a thread-pool worker, or any other
OS thread is always *deferred into the correct thread* — there is no cross-thread
switch to get wrong, and no per-release ``threading.Condition`` cost. Logging
from a thread pool "just works". (In the included benchmark, filament runs that
workload at ~15k msgs/s while both gevent and eventlet deadlock — even with
gevent's documented mitigations.)


Installation
============

Requirements:

* Python 2.7 or 3.8–3.13
* `greenlet <https://pypi.org/project/greenlet/>`_ (``>=0.4``; use the 1.1.x
  line for Python 2.7, 3.x otherwise)
* A C compiler and ``libevent`` development headers (Debian/Ubuntu:
  ``sudo apt-get install libevent-dev``). ``libbluetooth-dev`` is optional
  (Bluetooth socket support; compiled in only when the header is present).

Build the extensions in place::

    pip install greenlet
    python setup.py build_ext --inplace

Filament ships **seven** C extension modules under the ``_filament`` package
(``core``, ``io``, ``socket``, ``queue``, ``locking``, ``timer``, ``thrpool``);
the user-facing API is the pure-Python ``filament`` package layered on top.


Quick start
===========

Native API::

    import filament

    def worker(n):
        filament.sleep(0.01)
        return n * n

    # spawn returns a greenthread; .wait() joins it and returns the value
    # (or re-raises the exception raised inside it).
    gts = [filament.spawn(worker, i) for i in range(1000)]
    results = [gt.wait() for gt in gts]

    # Pools bound concurrency:
    pool = filament.GreenPool(50)
    for i in range(1000):
        pool.spawn(worker, i)
    pool.waitall()

    # Events, results, timeouts:
    ev = filament.Event()
    ar = filament.AsyncResult()
    with filament.Timeout(5.0):
        ...

    # Run a blocking call in a real OS-thread pool without blocking the hub:
    filament.tpool.execute(some_blocking_function, arg)

Cooperative sockets::

    from filament import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0)); srv.listen(128)

    def serve():
        while True:
            conn, _ = srv.accept()
            filament.spawn(handle, conn)   # one greenthread per connection


Monkey-patching
===============

Make the standard library cooperative (like ``gevent.monkey`` /
``eventlet.monkey_patch``)::

    import filament.patcher
    filament.patcher.patch_all()          # socket, ssl, select, os, time,
                                          # thread, threading, subprocess, queue

Granular patches are available too (``patch_socket``, ``patch_ssl``,
``patch_select``, ``patch_os``, ``patch_time``, ``patch_thread``,
``patch_subprocess``, ``patch_queue``), plus ``get_original``,
``is_module_patched``, and ``is_object_patched``.

``patch_thread(logging=True, existing_locks=True)`` converts the already-created
``logging`` locks (the module lock and every handler lock) to cooperative locks
while the process is still single-threaded — this, together with the scheduler
design above, is what keeps logging-from-a-thread-pool safe.


Drop-in gevent / eventlet
=========================

Filament can masquerade as ``gevent`` or ``eventlet`` without shadowing the real
packages on disk. Install the shim *before* importing under the target name::

    import filament.gevent_compat as gevent_compat
    gevent_compat.install()               # registers sys.modules['gevent'], etc.

    import gevent
    from gevent import monkey; monkey.patch_all()
    from gevent.pool import Pool
    from gevent.pywsgi import WSGIServer

...and similarly::

    import filament.eventlet_compat as eventlet_compat
    eventlet_compat.install()

    import eventlet
    from eventlet.green import socket
    pool = eventlet.GreenPool()

The shims cover the common surface: ``spawn``/``spawn_n``/``spawn_later``,
``Greenlet``/``GreenThread``, ``joinall``/``killall``, ``Event``/``AsyncResult``,
``Timeout``/``with_timeout``, ``Semaphore``/``lock``, ``Pool``/``Group``/
``GreenPool``/``GreenPile``, ``queue`` (incl. ``Channel``), ``monkey``/
``monkey_patch``, the ``green.*`` / ``gevent.socket`` etc. modules,
``tpool``/``threadpool``, ``hubs.trampoline``, a working ``StreamServer`` and a
minimal ``pywsgi``/``wsgi`` WSGI server. See the module docstrings for the
handful of documented stubs.


Feature parity
==============

Implemented, mapped onto filament's C core and native primitives:

* **Greenthreads:** ``spawn``, ``spawn_n``, ``spawn_later``/``spawn_after``,
  ``kill``/``killall``, ``joinall``, ``wait``/``iwait``, ``getcurrent``,
  ``sleep``, ``yield_thread``.
* **Sync/result:** ``Event``, ``AsyncResult``, ``Lock``, ``RLock``,
  ``Condition``, ``Semaphore``, ``Timeout``/``with_timeout``.
* **Pools:** ``Group``, ``Pool``, ``GreenPool``, ``GreenPile``.
* **Queues:** ``Queue``, ``SimpleQueue`` (C), plus pure-Python
  ``PriorityQueue``/``LifoQueue`` and gevent's ``Channel``.
* **Native-thread offload:** ``tpool.execute`` / ``tpool.Proxy`` (and a
  gevent-shaped ``ThreadPool``).
* **Cooperative stdlib:** ``socket``, ``ssl`` (modern ``SSLContext``),
  ``select`` (``select()``; ``poll`` raises a clear error), ``time``, ``os``
  (``read``/``write``), ``subprocess`` (cooperative ``wait``/``communicate``),
  ``threading`` (cooperative ``Thread``, greenlet-local ``local``), ``queue``.
* **Servers:** ``StreamServer`` and a minimal WSGI server via the compat shims.


Python version support
=======================

The same source builds and passes the full test suite (201 tests) on
**CPython 2.7.18, 3.8, 3.10, 3.12, and 3.13**. Python 2 vs 3 differences are
centralized in ``include/core/pyversion.h`` (string/int APIs, module init,
greenlet parent-reference ownership) rather than scattered through the C.


Benchmarks
==========

``benchmarks/`` contains a filament-vs-gevent-vs-eventlet suite (spawn
throughput, context-switch rate, semaphore/queue ops, thread-pool round-trip,
echo-server req/s + latency, and the #137 logging test), each framework run in
a fresh subprocess. Run it with::

    python benchmarks/run_all.py [--python /path/to/venv/bin/python]

Full numbers are in ``benchmarks/RESULTS.md``. Headlines (within-version ratios,
which are stable across the matrix):

* **Spawn throughput:** ~2–2.5× gevent/eventlet on 3.13, widening to ~4.4–4.7×
  on 3.10/3.8.
* **Context switches:** ~1.6× gevent, ~2.5× eventlet.
* **Semaphore/queue:** ~3–8× (C-level primitives).
* **#137 logging-from-threadpool:** filament completes (~15k msg/s); gevent and
  eventlet both **deadlock**.
* **Where filament trails:** thread-pool round-trip latency and raw echo-server
  req/s (gevent leads; filament's p99 tail at high concurrency is competitive).


Running the tests
=================

::

    python -m pytest tests/

The suite covers the native API, the cooperative stdlib, the patcher, and both
compat shims. ``tests/test_cross_thread_137.py`` is the regression test for the
bug described above: it logs from thread-pool workers while the hub runs
greenthreads and asserts there is no ``greenlet.error`` and no deadlock.


License
=======

MIT. Copyright (c) 2013–2014, Chris Behrens. See ``LICENSE``.
