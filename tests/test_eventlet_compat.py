# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
eventlet drop-in shim tests.

Each test runs in a fresh subprocess doing the documented
``import filament.eventlet_compat as ec; ec.install(); import eventlet`` dance,
then exercises the eventlet API.  Subprocess isolation keeps ``sys.modules`` /
``monkey_patch`` effects out of other tests.
"""

from __future__ import absolute_import

from tests._helpers import run_py

_INSTALL = (
    "import filament.eventlet_compat as ec\n"
    "ec.install()\n"
    "import eventlet\n"
)


def _check(script, timeout=25):
    res = run_py(_INSTALL + script, timeout=timeout)
    assert not res.timed_out, "subprocess timed out\n" + repr(res)
    assert res.returncode == 0, repr(res)
    assert "OK" in res.stdout, repr(res)
    return res


def test_eventlet_spawn_wait():
    _check('''
gt = eventlet.spawn(lambda a, b: a + b, 3, 4)
assert gt.wait() == 7

# exception is re-raised by wait()
gt2 = eventlet.spawn(lambda: (_ for _ in ()).throw(ValueError("boom")))
raised = False
try:
    gt2.wait()
except ValueError:
    raised = True
assert raised
print("OK")
''')


def test_eventlet_spawn_n_fire_and_forget():
    _check('''
box = []
eventlet.spawn_n(lambda: box.append("ran"))
eventlet.sleep(0)
eventlet.sleep(0)
assert box == ["ran"], box
print("OK")
''')


def test_eventlet_spawn_after():
    _check('''
box = []
h = eventlet.spawn_after(0.02, lambda: box.append("fired"))
assert box == []
h.wait()
assert box == ["fired"]
print("OK")
''')


def test_eventlet_greenpool_imap():
    _check('''
pool = eventlet.GreenPool(4)
results = list(pool.imap(lambda x: x * x, [1, 2, 3, 4, 5]))
assert results == [1, 4, 9, 16, 25], results
print("OK")
''')


def test_eventlet_greenpile():
    _check('''
from eventlet.greenpool import GreenPile
pile = GreenPile(4)
def work(i):
    eventlet.sleep(0.005 * ((6 - i) % 4))
    return i * 2
for i in range(8):
    pile.spawn(work, i)
assert list(pile) == [i * 2 for i in range(8)]
print("OK")
''')


def test_eventlet_event_send_wait():
    _check('''
from eventlet.event import Event
ev = Event()
eventlet.spawn(lambda: ev.send("delivered"))
assert ev.wait() == "delivered"
assert ev.ready() is True
print("OK")
''')


def test_eventlet_event_exception_via_get():
    # eventlet.event.Event maps onto filament.AsyncResult.  The exception a
    # producer sends is re-raised by get() (the gevent-shaped accessor).
    _check('''
from eventlet.event import Event
ev = Event()
eventlet.spawn(lambda: ev.send_exception(KeyError("k")))
raised = False
try:
    ev.get()
except KeyError:
    raised = True
assert raised
print("OK")
''')


def test_eventlet_event_wait_does_not_reraise_divergence():
    # DOCUMENTED DIVERGENCE: real eventlet's Event.wait() re-raises an exception
    # delivered via send_exception().  filament maps Event onto AsyncResult,
    # whose wait() intentionally returns the value (None here) and never
    # re-raises -- callers must use get() for the exception.  We pin the current
    # behavior so the divergence is visible and tracked.
    _check('''
from eventlet.event import Event
ev = Event()
eventlet.spawn(lambda: ev.send_exception(KeyError("k")))
result = ev.wait()          # eventlet would raise KeyError; AsyncResult returns None
assert result is None, result
print("OK")
''')


def test_eventlet_timeout():
    _check('''
from eventlet.timeout import Timeout
fired = []
try:
    with Timeout(0.02):
        eventlet.sleep(5)
except Timeout:
    fired.append(True)
assert fired == [True]
print("OK")
''')


def test_eventlet_queue():
    _check('''
from eventlet.queue import Queue, LifoQueue, PriorityQueue
q = Queue()
got = []
def consumer():
    got.append(q.get())
def producer():
    eventlet.sleep(0.01)
    q.put("x")
gc_ = eventlet.spawn(consumer)
gp = eventlet.spawn(producer)
gc_.wait(); gp.wait()
assert got == ["x"]

pq = PriorityQueue()
for x in (3, 1, 2):
    pq.put(x)
assert [pq.get() for _ in range(3)] == [1, 2, 3]
print("OK")
''')


def test_eventlet_semaphore():
    _check('''
from eventlet.semaphore import Semaphore, BoundedSemaphore
sem = Semaphore(2)
peak = [0]; cur = [0]
def worker():
    sem.acquire()
    cur[0] += 1
    if cur[0] > peak[0]:
        peak[0] = cur[0]
    eventlet.sleep(0.005)
    cur[0] -= 1
    sem.release()
gts = [eventlet.spawn(worker) for _ in range(8)]
for g in gts:
    g.wait()
assert peak[0] == 2, peak[0]

bs = BoundedSemaphore(1)
bs.acquire(); bs.release()
raised = False
try:
    bs.release()
except ValueError:
    raised = True
assert raised
print("OK")
''')


def test_eventlet_monkey_patch():
    _check('''
eventlet.monkey_patch()
import socket
assert "filament" in socket.__file__
assert eventlet.is_monkey_patched("socket") is True
print("OK")
''')


def test_eventlet_listen_serve_connect_echo():
    _check('''
server_sock = eventlet.listen(("127.0.0.1", 0))
addr = server_sock.getsockname()

def handle(client, client_addr):
    while True:
        data = client.recv(1024)
        if not data:
            break
        client.sendall(data)
    raise eventlet.StopServe()

# Run the accept/serve loop in a greenthread.
server_gt = eventlet.spawn(eventlet.serve, server_sock, handle)

client = eventlet.connect(addr)
client.sendall(b"eventlet-echo")
got = b""
while len(got) < len(b"eventlet-echo"):
    got += client.recv(100)
client.close()
assert got == b"eventlet-echo", got
print("OK")
''', timeout=25)


def test_eventlet_green_socket_echo():
    _check('''
from eventlet.green import socket as gsocket
import filament

q = []
def server():
    ls = gsocket.socket()
    ls.setsockopt(gsocket.SOL_SOCKET, gsocket.SO_REUSEADDR, 1)
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)
    q.append(ls.getsockname())
    conn, _ = ls.accept()
    conn.sendall(conn.recv(100))
    conn.close(); ls.close()

filament.spawn_n(server)
while not q:
    filament.sleep(0)
c = gsocket.socket()
c.connect(q[0])
c.sendall(b"green-sock")
got = c.recv(100)
c.close()
assert got == b"green-sock", got
print("OK")
''')


def test_eventlet_hubs_trampoline():
    _check('''
from eventlet import hubs
from eventlet.green import socket as gsocket
import filament

a, b = gsocket.socketpair()
def writer():
    filament.sleep(0.02)
    a.sendall(b"z")
filament.spawn_n(writer)
# trampoline blocks until b is read-ready.
hubs.trampoline(b.fileno(), read=True, timeout=1.0)
got = b.recv(1)
a.close(); b.close()
assert got == b"z", got
print("OK")
''')
