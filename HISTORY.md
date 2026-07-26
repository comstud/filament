# How filament began

In early 2013 while I was working on OpenStack, which used eventlet, we
discovered an occasional hang and sometimes some traceback spew from eventlet
about not being able to switch greenlets. I triaged it enough to determine
it happened while using eventlet's tpool (real OS threads pool) in combination
with calls to the logging library in python. The logging library makes use of
a threading lock, which eventlet had monkey patched.

I filed the issue (as **#137**) in eventlet's Bitbucket tracker — its home at
the time — on **February 17, 2013**, and tracked it down to eventlet's `Semaphore`
class not being safe across OS threads. Python's `logging` module guards its
handlers with a lock, and eventlet's monkey-patching converts that lock into
a `Semaphore` — so the net result was that you could not log from inside
a tpool (OS) thread without risking a deadlock. A workaround was available,
which was to monkey patch with thread=False. This avoided the patching of the
logging lock. On **February 18, 2013** I filed the bug with OpenStack Compute
(Nova), here: [bug #1128684](https://bugs.launchpad.net/nova/+bug/1128684),
noting the issue, citing the eventlet bitbucket issue number, and the
workaround.

I came up with a potential fix and submitted an initial pull request against
eventlet on **February 19**. It didn't fully fix the problem, and the more I
dug, the clearer it became that a *proper* fix inside eventlet would kill its
performance without rewriting `Semaphore` in C. I recall looking at gevent and
determining the same issue was there. OpenStack ended up simply working around
the bug — more than once — by avoiding logging inside tpools.

Meanwhile, I thought it would be fun to take a stab at the Semaphore in C and
that led to re-imagining the whole core in C. The main fix was to defer
greenlet switching to the greenlet's home scheduler in the OS thread where the
greenlet was started, vs a foreign thread trying to directly switch back to a
greenlet in another thread.

Filament was born. Tests showed a ~10x improvement against eventlet for
greenthread spawns at the time. Some work was done to implement queues and
a few other things. And then it sat in this mostly-working proof of concept
stage for 13 years. I moved on to other things and did not find the time to
finish it. Finally, with help from AI, it's a fully working project: a drop-in
replacement for both eventlet and gevent, with full support for synchronization
and queues between greenlets in multiple OS threads.

And it performs.

---

*Postscript: the same underlying bug was later re-reported against eventlet in
the GitHub era as issue #432 ("Semaphore does not work across different hubs
in different pthreads"), and it still bites gevent and eventlet today —
filament's test suite carries a regression test
(`tests/test_cross_thread_137.py`) that logs from thread-pool workers while
the hub runs greenthreads, and the benchmark suite runs the same workload
against all three libraries. Filament completes it; the other two deadlock.
See [README.md](README.md) for more about the design that makes this work.*
