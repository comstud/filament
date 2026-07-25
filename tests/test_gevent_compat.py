# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
gevent drop-in shim tests.

Each test runs in a fresh subprocess that does the documented
``import filament.gevent_compat as gc; gc.install(); import gevent`` dance and
then exercises the gevent API.  Subprocess isolation keeps the ``sys.modules``
registration (and any ``monkey.patch_all``) from leaking into other tests.
"""

from __future__ import absolute_import

from tests._helpers import run_py

_INSTALL = (
    "import filament.gevent_compat as gc\n"
    "gc.install()\n"
    "import gevent\n"
)


def _check(script, timeout=25):
    res = run_py(_INSTALL + script, timeout=timeout)
    assert not res.timed_out, "subprocess timed out\n" + repr(res)
    assert res.returncode == 0, repr(res)
    assert "OK" in res.stdout, repr(res)
    return res


def test_gevent_spawn_and_join():
    _check('''
results = []
gs = [gevent.spawn(lambda i=i: results.append(i)) for i in range(20)]
gevent.joinall(gs)
assert sorted(results) == list(range(20)), results
print("OK")
''')


def test_gevent_greenlet_get_value_and_exception():
    _check('''
from gevent import Greenlet

g = gevent.spawn(lambda: 42)
assert g.get() == 42
assert g.ready() is True
assert g.successful() is True
assert g.value == 42

g2 = gevent.spawn(lambda: (_ for _ in ()).throw(ValueError("boom")))
raised = False
try:
    g2.get()
except ValueError as e:
    raised = "boom" in str(e)
assert raised
assert g2.successful() is False
assert isinstance(g2.exception, ValueError)

# Deferred start via Greenlet(...).start()
g3 = Greenlet(lambda x: x * 2, 21)
assert g3.dead is True or g3.ready() is False
g3.start()
assert g3.get() == 42
print("OK")
''')


def test_gevent_spawn_later():
    _check('''
box = []
g = gevent.spawn_later(0.02, lambda: box.append("fired"))
assert box == []
g.join()
assert box == ["fired"]
print("OK")
''')


def test_gevent_timeout():
    _check('''
from gevent import Timeout
fired = []
try:
    with Timeout(0.02):
        gevent.sleep(5)
except Timeout:
    fired.append(True)
assert fired == [True]

# silent sentinel
with Timeout(0.02, False):
    gevent.sleep(5)
print("OK")
''')


def test_gevent_event_and_asyncresult():
    _check('''
from gevent.event import Event, AsyncResult

ev = Event()
got = []
gs = [gevent.spawn(lambda i=i: got.append((i, ev.wait()))) for i in range(5)]
gevent.sleep(0)
ev.set()
gevent.joinall(gs)
assert len(got) == 5 and all(r for _, r in got)

ar = AsyncResult()
gevent.spawn(lambda: ar.set(99))
assert ar.get() == 99

ar2 = AsyncResult()
gevent.spawn(lambda: ar2.set_exception(KeyError("k")))
raised = False
try:
    ar2.get()
except KeyError:
    raised = True
assert raised
print("OK")
''')


def test_gevent_lock():
    _check('''
from gevent.lock import Semaphore, BoundedSemaphore, RLock, DummySemaphore

sem = Semaphore(2)
peak = [0]; cur = [0]
def worker():
    sem.acquire()
    cur[0] += 1
    if cur[0] > peak[0]:
        peak[0] = cur[0]
    gevent.sleep(0.005)
    cur[0] -= 1
    sem.release()
gevent.joinall([gevent.spawn(worker) for _ in range(8)])
assert peak[0] == 2, peak[0]

bs = BoundedSemaphore(1)
bs.acquire(); bs.release()
try:
    bs.release()
    assert False
except ValueError:
    pass

d = DummySemaphore()
assert d.acquire() is True
d.release()
print("OK")
''')


def test_gevent_pool_map():
    _check('''
from gevent.pool import Pool, Group

p = Pool(4)
assert p.map(lambda x: x * x, [1, 2, 3, 4, 5]) == [1, 4, 9, 16, 25]

g = Group()
assert list(g.imap(lambda x: x + 1, [10, 20, 30])) == [11, 21, 31]
print("OK")
''')


def test_gevent_queue_and_channel():
    _check('''
from gevent.queue import Queue, PriorityQueue, LifoQueue, Channel, JoinableQueue

q = Queue()
got = []
def consumer():
    got.append(q.get())
def producer():
    gevent.sleep(0.01)
    q.put("item")
gevent.joinall([gevent.spawn(consumer), gevent.spawn(producer)])
assert got == ["item"]

pq = PriorityQueue()
for x in (5, 1, 3, 2, 4):
    pq.put(x)
assert [pq.get() for _ in range(5)] == [1, 2, 3, 4, 5]

lq = LifoQueue()
for x in range(3):
    lq.put(x)
assert [lq.get() for _ in range(3)] == [2, 1, 0]

# Channel: unbuffered rendezvous.
ch = Channel()
received = []
def ch_consumer():
    received.append(ch.get())
def ch_producer():
    ch.put("rendezvous")
gevent.joinall([gevent.spawn(ch_consumer), gevent.spawn(ch_producer)])
assert received == ["rendezvous"]

# Channel: gevent API surface.
from gevent.queue import Empty, Full

ch = Channel(1)          # maxsize=1 is accepted...
try:
    Channel(2)           # ...anything else is not
    assert False
except ValueError:
    pass

assert ch.qsize() == 0
assert ch.empty() is True
assert ch.full() is True
assert ch.balance == 0

# Non-blocking ops with no peer waiting.
try:
    ch.put_nowait("x")
    assert False, "put_nowait with no getter must raise Full"
except Full:
    pass
try:
    ch.get_nowait()
    assert False, "get_nowait with no putter must raise Empty"
except Empty:
    pass

# put_nowait succeeds when a getter is already waiting.
got = []
g = gevent.spawn(lambda: got.append(ch.get()))
gevent.sleep(0.01)       # let the getter park
assert ch.balance == -1
ch.put_nowait("handoff")
g.join()
assert got == ["handoff"]

# get_nowait succeeds when a putter is already waiting.
p = gevent.spawn(ch.put, "waiting-put")
gevent.sleep(0.01)       # let the putter park
assert ch.balance == 1
assert ch.get_nowait() == "waiting-put"
p.join()
assert p.successful()

# Timeouts raise Full / Empty.
import time as _time
t0 = _time.time()
try:
    ch.put("nope", timeout=0.05)
    assert False
except Full:
    pass
assert _time.time() - t0 < 5
try:
    ch.get(timeout=0.05)
    assert False
except Empty:
    pass
assert ch.balance == 0   # timed-out waiters cleaned themselves up

# Iteration terminates on the StopIteration sentinel.
def feed():
    for x in (1, 2, 3):
        ch.put(x)
    ch.put(StopIteration)
gevent.spawn(feed)
assert [x for x in ch] == [1, 2, 3]
print("OK")
''')


def test_gevent_threadpool():
    _check('''
from gevent.threadpool import ThreadPool
import time as _t

tp = ThreadPool(4)
# apply blocks for the result
assert tp.apply(lambda a, b: a + b, (3, 4)) == 7
# spawn returns a Greenlet future
g = tp.spawn(lambda: 21 * 2)
assert g.get() == 42
# map
assert tp.map(lambda x: x * 10, [1, 2, 3]) == [10, 20, 30]
print("OK")
''')


def test_gevent_monkey_patch_all():
    _check('''
from gevent import monkey
real_socket = monkey.get_original("socket", "socket")
monkey.patch_all()
import socket
assert "filament" in socket.__file__
assert monkey.is_module_patched("socket") is True
# get_original still returns the real class captured before patching.
assert monkey.get_original("socket", "socket") is real_socket
print("OK")
''')


def test_gevent_streamserver_echo():
    _check('''
from gevent.server import StreamServer

def handle(sock, addr):
    while True:
        data = sock.recv(1024)
        if not data:
            break
        sock.sendall(data)

server = StreamServer(("127.0.0.1", 0), handle)
server.start()
host, port = server.server_host, server.server_port

import filament.socket as fsocket
c = fsocket.create_connection((host, port))
c.sendall(b"stream-echo")
got = b""
while len(got) < len(b"stream-echo"):
    got += c.recv(100)
c.close()
server.stop()
assert got == b"stream-echo", got
print("OK")
''', timeout=25)


# ---------------------------------------------------------------------------
# Regression tests for the gevent-parity audit fixes (Timeout base class,
# AsyncResult/Waiter, queue surface, locks, Greenlet lifecycle, joinall/wait,
# pool slots/ordering, threadpool, server/pywsgi).
# ---------------------------------------------------------------------------

def test_gevent_timeout_semantics():
    _check('''
# gevent.Timeout is a BaseException so "except Exception" can't eat it.
assert not issubclass(gevent.Timeout, Exception)

caught = []
try:
    try:
        with gevent.Timeout(0.02):
            gevent.sleep(1)
    except Exception:
        caught.append("wrong")
except gevent.Timeout:
    caught.append("right")
assert caught == ["right"], caught

# String-payload Timeout fires and carries the message.
try:
    with gevent.Timeout(0.02, "custom message"):
        gevent.sleep(1)
    assert False, "did not fire"
except gevent.Timeout as t:
    assert "custom message" in str(t), str(t)
print("OK")
''')


def test_gevent_asyncresult_and_waiter():
    _check('''
from gevent.event import AsyncResult
from gevent.hub import Waiter

# get(timeout=) raises a Timeout catchable as gevent.Timeout.
a = AsyncResult()
try:
    a.get(timeout=0.02)
    assert False
except gevent.Timeout:
    pass

# set() overwrites (gevent), including over a stored exception.
a2 = AsyncResult()
a2.set(1); a2.set(2)
assert a2.get() == 2
a2.set_exception(ValueError("x"))
try:
    a2.get(); assert False
except ValueError:
    pass
a2.set(3)
assert a2.get() == 3 and a2.successful()

# AsyncResult is a link target (the __call__ protocol).
res = AsyncResult()
gevent.spawn(lambda: 42).link(res)
assert res.get(timeout=1) == 42

# Waiter: silent overwrite + greenlet-style throw forms.
w = Waiter(); w.switch(1); w.switch(2)
assert w.get() == 2
w2 = Waiter(); w2.throw()
try:
    w2.get(); assert False
except gevent.GreenletExit:
    pass
w3 = Waiter(); w3.throw(ValueError, "tv")
try:
    w3.get(); assert False
except ValueError as e:
    assert e.args == ("tv",), e.args
print("OK")
''')


def test_gevent_queue_surface():
    _check('''
from gevent.queue import Queue, SimpleQueue, PriorityQueue, LifoQueue, Empty, Full

# None is gevent's default maxsize and must be accepted everywhere.
Queue(None); Queue(maxsize=None); SimpleQueue(5); PriorityQueue(None); LifoQueue(None)

# Queues are unconditionally truthy and support len().
q = Queue()
assert bool(q) is True and len(q) == 0
assert bool(PriorityQueue()) is True and len(LifoQueue()) == 0

# Iteration ends on the StopIteration sentinel.
q.put(1); q.put(2); q.put(StopIteration)
assert [x for x in q] == [1, 2]

# join() returns True; join(timeout=) returns False while busy.
jq = Queue()
jq.put("a")
assert jq.join(timeout=0.02) is False
jq.get(); jq.task_done()
assert jq.join() is True
pq = PriorityQueue()
pq.put(1)
assert pq.join(timeout=0.02) is False
pq.get(); pq.task_done()
assert pq.join() is True
print("OK")
''')


def test_gevent_lock_semantics():
    _check('''
from gevent.lock import Semaphore, BoundedSemaphore, RLock

s = Semaphore(1)
assert s.acquire() is True
assert s.acquire(blocking=False) is False       # no SystemError, no raise
assert s.acquire(timeout=0.02) is False         # timeout returns False
assert s.release() == 1
assert s.locked() is False and s.counter == 1
with s:                                          # context manager works
    assert s.locked() is True

bs = BoundedSemaphore(1)
assert bs.acquire() is True
assert bs.acquire(blocking=False) is False       # not a Timeout raise
assert bs.acquire(timeout=0.02) is False
bs.release()
try:
    bs.release(); assert False
except ValueError:
    pass

r = RLock()
r.acquire()
assert r.locked() is True                        # held by us still counts
r.release()
assert r.locked() is False
print("OK")
''')


def test_gevent_greenlet_lifecycle():
    _check('''
from gevent import Greenlet

# kill() before start: dead, successful, value=GreenletExit, never runs.
ran = []
g = Greenlet(lambda: ran.append(1))
g.kill()
assert g.ready() and g.successful() and isinstance(g.value, gevent.GreenletExit)
assert g.dead
g.start(); gevent.sleep(0.02)
assert ran == []

# kill(exception=E) on a pending start_later records a *failure*.
g2 = gevent.spawn_later(5, lambda: None)
g2.kill(exception=ValueError("nope"))
assert not g2.successful() and isinstance(g2.exception, ValueError)

# dead/bool through the lifecycle.
u = Greenlet(lambda: None)
assert u.dead is False and bool(u) is False      # fresh: not dead, falsy
u.start()
assert bool(u) is True
u.join()
assert u.dead is True and bool(u) is False

# join/get on a not-yet-started greenlet waits rather than returning.
import time
u2 = Greenlet(lambda: "late")
t0 = time.time()
u2.join(timeout=0.05)
assert time.time() - t0 >= 0.04
try:
    u2.get(block=False); assert False
except gevent.Timeout as t:
    assert t.seconds is None                     # bare Timeout like gevent

# gevent.kill is asynchronous.
v = gevent.spawn(gevent.sleep, 5)
gevent.sleep(0)
gevent.kill(v)
assert v.ready() is False
gevent.sleep(0.05)
assert v.ready() is True
print("OK")
''')


def test_gevent_joinall_wait_iwait():
    _check('''
import time

# joinall(raise_error=True) re-raises.
def boom():
    raise ValueError("boom")
try:
    gevent.joinall([gevent.spawn(boom)], raise_error=True)
    assert False
except ValueError:
    pass

# joinall(timeout=) honours the deadline and returns the finished subset.
t0 = time.time()
gs = [gevent.spawn(gevent.sleep, 5), gevent.spawn(gevent.sleep, 5)]
done = gevent.joinall(gs, timeout=0.1)
assert time.time() - t0 < 1 and done == []
gevent.killall(gs)

# joinall(count=) returns early with the first finisher.
fast = gevent.spawn(lambda: "f"); slow = gevent.spawn(gevent.sleep, 5)
done = gevent.joinall([slow, fast], count=1)
assert len(done) == 1 and done[0] is fast
gevent.kill(slow)

# wait() actually waits; iwait yields in completion order.
g = gevent.spawn(gevent.sleep, 0.1)
t0 = time.time()
gevent.wait([g], timeout=2)
assert g.ready() and time.time() - t0 >= 0.09
a = gevent.spawn(gevent.sleep, 0.1); b = gevent.spawn(gevent.sleep, 0.01)
order = list(gevent.iwait([a, b]))
assert order[0] is b and order[1] is a

# killall raises Timeout when a greenlet refuses to die in time.
def stubborn():
    while True:
        try:
            gevent.sleep(1)
        except gevent.GreenletExit:
            pass
s = gevent.spawn(stubborn)
try:
    gevent.killall([s], timeout=0.1)
    assert False
except gevent.Timeout:
    pass
print("OK")
''')


def test_gevent_pool_semantics():
    _check('''
import time
from gevent.pool import Pool, Group, PoolFull

# Group takes a single iterable (gevent ctor shape).
g1 = gevent.spawn(lambda: 1); g2 = gevent.spawn(lambda: 2)
grp = Group([g1, g2])
gevent.joinall([g1, g2]); gevent.sleep(0.01)
assert len(grp) == 0                        # auto-discard on completion

# imap_unordered yields in completion order and starts work eagerly.
p = Pool(3)
def work(x):
    gevent.sleep(0.1 if x == 0 else 0.01)
    return x
assert list(p.imap_unordered(work, [0, 1, 2])) == [1, 2, 0]
assert list(p.imap(work, [0, 1, 2])) == [0, 1, 2]
assert list(p.imap(lambda x: x * 2, [1, 2, 3], maxsize=1)) == [2, 4, 6]

# join returns a bool.
p2 = Pool(2)
p2.spawn(gevent.sleep, 5)
assert p2.join(timeout=0.05) is False
p2.kill()
assert p2.join() is True and len(p2) == 0

# add consumes a slot; non-blocking add on a full pool raises PoolFull;
# discard releases the slot.
p3 = Pool(1)
gx = gevent.spawn(gevent.sleep, 0.3)
p3.add(gx)
assert p3.free_count() == 0 and p3.full()
try:
    p3.add(gevent.spawn(lambda: None), blocking=False)
    assert False
except PoolFull:
    pass
p3.discard(gx)
assert p3.free_count() == 1
gx.kill()

# kill() works on gevent.spawn'ed members added via add().
p5 = Pool()
gz = gevent.spawn(gevent.sleep, 5)
p5.add(gz)
p5.kill()
assert gz.ready()

# wait_available returns the free count, or 0 on timeout, never raises.
p6 = Pool(1)
p6.spawn(gevent.sleep, 0.1)
assert p6.wait_available(timeout=0.02) == 0
assert p6.wait_available(timeout=2) == 1

# spawn returns a gevent-shaped Greenlet; Pool(-1) is rejected.
assert Pool(2).spawn(lambda: 42).get() == 42
try:
    Pool(-1); assert False
except ValueError:
    pass
print("OK")
''')


def test_gevent_threadpool_join_imap():
    _check('''
import time as _t
from gevent.threadpool import ThreadPool

tp = ThreadPool(4)
out = []
tp.spawn(lambda: (_t.sleep(0.15), out.append(1)))
tp.join()                                        # must wait for the task
assert out == [1]
assert list(tp.imap(lambda a, b: a + b, [1, 2], [10, 20])) == [11, 22]
assert list(tp.imap(lambda x: x, [1, 2], maxsize=1)) == [1, 2]
assert sorted(tp.imap_unordered(lambda x: x, [3, 4])) == [3, 4]
assert tp.size == 4
print("OK")
''')


def test_gevent_pywsgi_post_and_environ():
    _check('''
from gevent.pywsgi import WSGIServer
import filament.socket as fsocket

seen = {}
def app(environ, start_response):
    seen["path"] = environ["PATH_INFO"]
    seen["custom"] = environ.get("X_CUSTOM")
    body = environ["wsgi.input"].read()          # must NOT hang on POST
    start_response("200 OK", [("Content-Type", "text/plain"),
                              ("Content-Length", str(len(body) + 5))])
    return [b"BODY=", body]

srv = WSGIServer(("127.0.0.1", 0), app, environ={"X_CUSTOM": "yes"})
srv.start()
c = fsocket.create_connection(("127.0.0.1", srv.address[1]))
c.sendall(b"POST /a%20b HTTP/1.1\\r\\nHost: x\\r\\nContent-Length: 5\\r\\n\\r\\nhello")
resp = b""
with gevent.Timeout(5):
    while True:
        chunk = c.recv(4096)
        if not chunk:
            break
        resp += chunk
c.close()
srv.stop()
assert b"BODY=hello" in resp, resp
assert seen["path"] == "/a b", seen              # percent-decoded
assert seen["custom"] == "yes"                   # environ= merged
assert srv.log is not None and srv.error_log is not None

# ssl kwargs are forwarded, not silently swallowed.
srv2 = WSGIServer(("127.0.0.1", 0), app, keyfile="/k", certfile="/c")
assert srv2._ssl_args == {"keyfile": "/k", "certfile": "/c"}
print("OK")
''')


def test_gevent_streamserver_backlog_and_stop():
    _check('''
import time
from gevent.server import StreamServer
import filament.socket as fsocket

# Third positional argument is backlog (gevent order), not spawn.
ss = StreamServer(("127.0.0.1", 0), lambda s, a: None, 128)
assert ss.backlog == 128 and ss.pool is None

# stop(timeout) kills in-flight pooled handlers after the grace period.
finished = []
def slow_handler(sock, addr):
    gevent.sleep(10)
    finished.append(1)

ss2 = StreamServer(("127.0.0.1", 0), slow_handler, spawn=2)
ss2.start()
c = fsocket.create_connection(("127.0.0.1", ss2.address[1]))
gevent.sleep(0.05)
t0 = time.time()
ss2.stop(timeout=0.1)
assert time.time() - t0 < 2 and finished == []
c.close()
print("OK")
''')
