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
