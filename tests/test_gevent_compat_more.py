# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
gevent drop-in shim tests -- coverage-gap additions.

Same pattern as test_gevent_compat.py: each scenario runs in a fresh
subprocess doing the documented ``import filament.gevent_compat as gc;
gc.install(); import gevent`` dance, because install() mutates sys.modules
process-globally.  These tests target the previously-uncovered paths: the
individual monkey.patch_* helpers, the Hub/Waiter surface, StreamServer spawn
strategies + serve_forever/stop + SSL, queue/Channel edges, Group/Pool edges
and pywsgi request handling.
"""

from __future__ import absolute_import

import pytest

from tests._helpers import run_py, make_self_signed_cert

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


def test_gevent_monkey_individual_patches():
    # Each granular patch_* helper greens its corresponding stdlib module.
    _check('''
from gevent import monkey

assert monkey.is_module_patched("socket") is False
monkey.patch_socket()
import socket
assert "filament" in socket.__file__, socket.__file__

monkey.patch_ssl()
import ssl
assert "filament" in ssl.__file__, ssl.__file__

monkey.patch_select()
import select
assert "filament" in select.__file__, select.__file__

monkey.patch_os()
monkey.patch_time()
import time
assert "filament" in time.__file__, time.__file__

monkey.patch_subprocess()
monkey.patch_queue()
monkey.patch_dns()
monkey.patch_thread()

assert monkey.is_module_patched("socket") is True
assert monkey.is_module_patched("time") is True
assert monkey.is_module_patched("select") is True
assert monkey.is_module_patched("ssl") is True
assert "socket" in monkey.saved
# The captured original is the REAL stdlib sleep, not the green one.
assert monkey.get_original("time", "sleep") is not time.sleep
print("OK")
''')


def test_gevent_monkey_patch_all_gevent_only_flags():
    # gevent-only toggles (sys/Event/signal/builtins + unknown kwargs) are
    # accepted and ignored; the filament-supported subsystems still patch.
    _check('''
from gevent import monkey
monkey.patch_all(sys=True, Event=True, signal=False, builtins=False,
                 some_future_flag=True)
import socket, time
assert "filament" in socket.__file__
assert "filament" in time.__file__
assert monkey.is_module_patched("socket") is True
print("OK")
''')


def test_gevent_hub_surface_and_waiter():
    _check('''
from gevent import hub as hub_mod
from gevent.hub import Waiter, get_hub, get_hub_if_exists

hub_mod.sleep(0)
h = get_hub()
assert get_hub() is h and get_hub_if_exists() is h
assert h.greenlet is not None
h.switch()
h.sleep(0)
g = h.spawn(lambda: 5)
assert g.wait() == 5

# loop stub: run_callback schedules fire-and-forget.
box = []
assert h.loop.run_callback(box.append, 3) is None
gevent.sleep(0.05)
assert box == [3], box

# threadpool: lazily created, cached, and genuinely works.
tp = h.threadpool
assert h.threadpool is tp
assert tp.apply(lambda a, b: a + b, (4, 7)) == 11
# Shut the backing OS-thread pool down so the interpreter can exit (the
# lazily-created default filament tpool otherwise keeps worker threads
# alive past the end of the script).
tp.kill()

# Waiter: switch_args + ready.
w = Waiter()
assert w.ready() is False
w.switch_args(1, 2)
assert w.get() == (1, 2)
assert w.ready() is True

def expect(waiter, exc_type, args=None):
    try:
        waiter.get()
        assert False, "did not raise"
    except BaseException as e:
        assert isinstance(e, exc_type), e
        if args is not None:
            assert e.args == args, e.args

# throw(type, instance)
w1 = Waiter(); w1.throw(ValueError, ValueError("inst"))
expect(w1, ValueError, ("inst",))
# throw(type) -> instantiated bare
w2 = Waiter(); w2.throw(ValueError)
expect(w2, ValueError, ())
# throw(type, tuple) -> type(*tuple)
w3 = Waiter(); w3.throw(ValueError, (1, 2))
expect(w3, ValueError, (1, 2))
# throw(type, scalar) -> type(scalar)
w4 = Waiter(); w4.throw(ValueError, "msg")
expect(w4, ValueError, ("msg",))
# throw(type, value, tb)
w5 = Waiter(); w5.throw(ValueError, ValueError("tb"), None)
expect(w5, ValueError, ("tb",))
# throw(instance[, tb])
w6 = Waiter(); w6.throw(KeyError("k"), None)
expect(w6, KeyError)
# throw() -> GreenletExit
w7 = Waiter(); w7.throw()
expect(w7, gevent.GreenletExit)
print("OK")
''')


def test_gevent_server_spawn_strategies():
    _check('''
from gevent.server import StreamServer
from gevent.pool import Pool
import filament.socket as fsocket

def echo(sock, addr):
    sock.sendall(sock.recv(100))

# spawn=None: handled inline in the accept greenlet.
s1 = StreamServer(("127.0.0.1", 0), echo, spawn=None)
assert s1._inline is True and s1.pool is None
s1.start()
s1.start()                              # second start is a no-op
c = fsocket.create_connection(s1.address)
c.sendall(b"inline")
assert c.recv(100) == b"inline"
c.close()
s1.close()                              # close() is an alias for stop()
assert s1.started is False

# spawn=<Pool instance>: used directly.
p = Pool(2)
s2 = StreamServer(("127.0.0.1", 0), echo, spawn=p)
assert s2.pool is p
s2.start()
c = fsocket.create_connection(s2.address)
c.sendall(b"pooled")
assert c.recv(100) == b"pooled"
c.close()
s2.stop()

# Non-tuple listener without getsockname: address falls back to None.
class FakeListener(object):
    pass
s3 = StreamServer(FakeListener(), echo)
assert s3.address is None
assert s3.server_host is None and s3.server_port is None
print("OK")
''')


def test_gevent_server_serve_forever_and_stop():
    _check('''
from gevent.server import StreamServer
import filament.socket as fsocket

def echo(sock, addr):
    sock.sendall(sock.recv(100))

srv = StreamServer(("127.0.0.1", 0), echo, spawn=4)

def stopper():
    gevent.sleep(0.05)
    c = fsocket.create_connection(srv.address)
    c.sendall(b"sf")
    assert c.recv(100) == b"sf"
    c.close()
    srv.stop(timeout=1)

g = gevent.spawn(stopper)
srv.serve_forever()             # blocks until stop() from the other greenlet
g.get()
assert srv.started is False
print("OK")
''')


def test_gevent_server_accept_error_paths():
    _check('''
import filament
from gevent.server import StreamServer

# A non-GreenletExit error in accept AFTER stop was flagged ends the loop.
srv = StreamServer(("127.0.0.1", 0), lambda s, a: None)
srv.start()
gevent.sleep(0.05)
srv._stopped = True
acc = srv._accept_greenlet
filament.kill(acc, RuntimeError("listener gone"))
gevent.sleep(0.05)
assert acc.dead
srv.socket.close()

# The same error WITHOUT the stop flag propagates (accept greenlet dies).
srv2 = StreamServer(("127.0.0.1", 0), lambda s, a: None)
srv2.start()
gevent.sleep(0.05)
acc2 = srv2._accept_greenlet
filament.kill(acc2, RuntimeError("unexpected"))
gevent.sleep(0.05)
assert acc2.dead
srv2.socket.close()
print("OK")
''')


def test_gevent_server_ssl():
    certfile, keyfile = make_self_signed_cert()
    if certfile is None:
        pytest.skip("no self-signed cert generator available")
    _check('''
from gevent.server import StreamServer
import filament.socket as fsocket
import filament.ssl as fssl

def echo(sock, addr):
    sock.sendall(sock.recv(100))

srv = StreamServer(("127.0.0.1", 0), echo, keyfile=%r, certfile=%r)
srv.start()
raw = fsocket.create_connection(srv.address)
c = fssl.wrap_socket(raw)
c.sendall(b"tls-echo")
got = c.recv(100)
c.close()
srv.stop()
assert got == b"tls-echo", got
print("OK")
''' % (keyfile, certfile))


def test_gevent_queue_more():
    _check('''
import collections
from gevent.queue import (Queue, JoinableQueue, SimpleQueue, PriorityQueue,
                          LifoQueue, Channel, Empty, Full)
from gevent.queue import _safe_remove

# _safe_remove tolerates an already-removed item.
d = collections.deque([1])
_safe_remove(d, 99)             # absent -> swallowed ValueError
_safe_remove(d, 1)
assert len(d) == 0

# JoinableQueue task_done/join.
jq = JoinableQueue()
jq.put("a")
def worker():
    jq.get()
    jq.task_done()
gevent.spawn(worker)
assert jq.join(timeout=2) is True

# SimpleQueue accepts a maxsize (gevent 25.x shape).
sq = SimpleQueue(2)
sq.put(1)
assert sq.get() == 1

# LIFO/priority disciplines.
pq = PriorityQueue(); lq = LifoQueue()
for x in (2, 1, 3):
    pq.put(x)
    lq.put(x)
assert [pq.get() for _ in range(3)] == [1, 2, 3]
assert [lq.get() for _ in range(3)] == [3, 1, 2]

# Empty/Full edges on the FIFO queue.
q = Queue(1)
q.put_nowait("x")
try:
    q.put_nowait("y"); assert False
except Full:
    pass
try:
    Queue().get_nowait(); assert False
except Empty:
    pass

# Channel accepts only maxsize=1.
try:
    Channel(2); assert False
except ValueError:
    pass

# Channel repr/str reflect parked peers.
ch = Channel()
assert "Channel" in repr(ch) and "Channel" in str(ch)
g = gevent.spawn(ch.get)
gevent.sleep(0.01)
assert "getters[1]" in repr(ch), repr(ch)
ch.put("v")
assert g.get() == "v"
p = gevent.spawn(ch.put, "w")
gevent.sleep(0.01)
assert "putters[1]" in str(ch), str(ch)
assert ch.get() == "w"
p.join()

# Non-blocking / timed forms raise Full/Empty; iteration ends on sentinel.
try:
    ch.put_nowait("x"); assert False
except Full:
    pass
try:
    ch.get_nowait(); assert False
except Empty:
    pass
try:
    ch.put("t", timeout=0.05); assert False
except Full:
    pass
try:
    ch.get(timeout=0.05); assert False
except Empty:
    pass

def feed():
    for x in (1, 2):
        ch.put(x)
    ch.put(StopIteration)
gevent.spawn(feed)
assert [x for x in ch] == [1, 2]
assert ch.qsize() == 0 and ch.empty() is True and ch.full() is True
assert ch.balance == 0
print("OK")
''')


def test_gevent_pool_more():
    _check('''
import filament
from gevent import Greenlet
from gevent.pool import Group, Pool, PoolFull

# Group() takes at most one iterable.
try:
    Group([], []); assert False
except TypeError:
    pass

grp = Group()
g = gevent.spawn(gevent.sleep, 0.05)
grp.add(g)
grp.add(g)                          # duplicate add is a no-op
assert g in grp
assert list(iter(grp)) == [g]
assert "Group" in repr(grp)
assert len(grp) == 1
assert grp.full() is False
assert grp.wait_available() == 1
assert grp.join() is True           # no-timeout join drains the group
assert len(grp) == 0

# killone kills only a tracked member; untracked is a no-op.
grp2 = Group()
s = grp2.spawn(gevent.sleep, 5)
grp2.killone(s)
assert s.ready() and len(grp2) == 0
grp2.killone(s)

# A raw filament greenthread (no .link/.ready) can still be grouped/joined.
grp3 = Group()
raw = filament.spawn(filament.sleep, 0.05)
grp3.add(raw)
assert grp3.join(timeout=2) is True
assert len(grp3) == 0

# imap/imap_unordered reject unknown kwargs.
try:
    Group().imap(lambda x: x, [1], bogus=1); assert False
except TypeError:
    pass
try:
    Group().imap_unordered(lambda x: x, [1], bogus=1); assert False
except TypeError:
    pass

# A worker exception is re-raised at the point its result is consumed.
def bad(x):
    if x == 2:
        raise KeyError("k2")
    return x
it = Pool(2).imap(bad, [1, 2, 3])
assert next(it) == 1
try:
    next(it); assert False
except KeyError:
    pass
try:
    for _ in Pool(2).imap_unordered(bad, [2]):
        pass
    assert False
except KeyError:
    pass

# map is ordered.
assert Pool(3).map(lambda x: x + 1, [1, 2, 3]) == [2, 3, 4]

# Custom greenlet_class is honoured.
class MyG(Greenlet):
    pass
p2 = Pool(2, greenlet_class=MyG)
gg = p2.spawn(lambda: 9)
assert isinstance(gg, MyG) and gg.get() == 9

# Unbounded pool capacity accessors.
p3 = Pool()
assert p3.free_count() == 1
assert p3.wait_available() == 1
gz = p3.spawn(lambda: 1)
p3.add(gz)                          # duplicate add returns early
assert gz.get() == 1

# Bounded slots: full()/PoolFull/discard.
p4 = Pool(1)
ga = gevent.spawn(gevent.sleep, 0.2)
p4.add(ga)
assert p4.full() is True and p4.free_count() == 0
try:
    p4.add(gevent.spawn(lambda: None), blocking=False); assert False
except PoolFull:
    pass
p4.discard(ga)
assert p4.free_count() == 1
ga.kill()

# join(timeout) returns False while busy; kill empties the pool.
p5 = Pool(2)
p5.spawn(gevent.sleep, 5)
assert p5.join(timeout=0.05) is False
p5.kill()
assert p5.join() is True and len(p5) == 0
print("OK")
''')


def test_gevent_greenlet_edges():
    _check('''
from gevent import Greenlet

# spawn_raw validates callables and returns a raw filament greenthread.
try:
    gevent.spawn_raw(42); assert False
except TypeError:
    pass
raw = gevent.spawn_raw(gevent.sleep, 5)
gevent.sleep(0.01)
gevent.kill(raw)                    # non-Greenlet branch (async)
gevent.sleep(0.05)
assert raw.dead
gevent.kill(raw)                    # killing a dead raw greenlet: no-op

# Greenlet constructor validates the run argument.
try:
    Greenlet(42); assert False
except TypeError:
    pass

# link_value / link_exception fire only on the matching outcome.
hits = []
gok = gevent.spawn(lambda: 1)
gok.link_value(lambda g: hits.append(("v", g.value)))
gok.link_exception(lambda g: hits.append(("e", None)))
gbad = gevent.spawn(lambda: (_ for _ in ()).throw(ValueError("b")))
gbad.link_value(lambda g: hits.append(("v2", None)))
gbad.link_exception(lambda g: hits.append(("e2", str(g.exception))))
gevent.joinall([gok, gbad])
gevent.sleep(0.05)
assert ("v", 1) in hits and ("e2", "b") in hits, hits
assert not any(h[0] in ("e", "v2") for h in hits), hits

# get(timeout=) raises a timeout on expiry.  Matched by class NAME: under
# coverage's subprocess bootstrap the C core ends up with a duplicate
# filament.exc.Timeout class object, so the raised instance is not always
# an instance of gevent.Timeout (a class-identity measurement quirk, not a
# behavior change -- outside coverage it IS a gevent.Timeout).
gslow = gevent.spawn(gevent.sleep, 5)
raised = None
try:
    gslow.get(timeout=0.05)
except BaseException as e:
    raised = e
assert raised is not None and "Timeout" in type(raised).__name__, repr(raised)
gslow.kill()

# killall(block=False) schedules the kills asynchronously.
gs = [gevent.spawn(gevent.sleep, 5) for _ in range(2)]
gevent.sleep(0.01)
gevent.killall(gs, block=False)
gevent.sleep(0.1)
assert all(g.ready() for g in gs)

# wait() with no objects: documented [] stub.
assert gevent.wait() == []

# kill of a pending spawn_later cancels the start; GreenletExit is the value.
gl = gevent.spawn_later(5, lambda: None)
gevent.kill(gl)
gevent.sleep(0.05)
assert gl.ready() and isinstance(gl.value, gevent.GreenletExit)
print("OK")
''')


def test_gevent_pywsgi_more():
    _check('''
from gevent.pywsgi import WSGIServer
import filament.socket as fsocket

calls = {}

class Result(list):
    def close(self):
        calls["closed"] = True

def app(environ, start_response):
    calls["query"] = environ["QUERY_STRING"]
    calls["hdr"] = environ.get("HTTP_X_THING")
    calls["ct"] = environ.get("CONTENT_TYPE")
    calls["lines"] = environ["wsgi.input"].readlines()
    write = start_response("200 OK", [("Content-Type", "text/plain")])
    write(b"part1|")                        # legacy write() path
    return Result([b"part2"])

srv = WSGIServer(("127.0.0.1", 0), app, log=None, error_log=None)
srv.start()

def fetch(req):
    # Read-until-EOF, so ask for the connection to be closed; keep-alive and
    # chunked framing get their own coverage in tests/test_gevent_compat.py.
    if b"Connection:" not in req:
        req = req.replace(b"\\r\\n\\r\\n", b"\\r\\nConnection: close\\r\\n\\r\\n", 1)
    c = fsocket.create_connection(("127.0.0.1", srv.address[1]))
    c.sendall(req)
    resp = b""
    with gevent.Timeout(5):
        while True:
            try:
                chunk = c.recv(4096)
            except Exception:
                break              # py2 may RST after the response is sent
            if not chunk:
                break
            resp += chunk
    c.close()
    return resp

# POST with query string, custom header, colon-less junk header line and body.
resp = fetch(b"POST /p?a=1&b=2 HTTP/1.1\\r\\nHost: x\\r\\nX-Thing: zap\\r\\n"
             b"Bogus-line-without-colon\\r\\nContent-Type: text/plain\\r\\n"
             b"Content-Length: 8\\r\\n\\r\\nl1\\nl2\\nxx")
assert b"part1|part2" in resp, resp
assert calls["query"] == "a=1&b=2" and calls["hdr"] == "zap", calls
assert calls["ct"] == "text/plain"
assert calls["lines"] == [b"l1\\n", b"l2\\n", b"xx"], calls["lines"]
assert calls.get("closed") is True

# Malformed request line -> 400.
resp = fetch(b"BADREQUEST\\r\\n\\r\\n")
assert b"400" in resp, resp

# Bounded Input.read(n) / read() and an empty-iterable app.
def app2(environ, start_response):
    inp = environ["wsgi.input"]
    calls["first"] = inp.read(3)
    calls["rest"] = inp.read()
    calls["eof"] = inp.read()
    start_response("204 No Content", [])
    return []
srv.application = app2
resp = fetch(b"GET /done HTTP/1.1\\r\\nHost: x\\r\\n"
             b"Content-Length: 5\\r\\n\\r\\nhello")
assert b"204" in resp, resp
assert calls["first"] == b"hel" and calls["rest"] == b"lo"
assert calls["eof"] == b""
srv.stop()
print("OK")
''')


def test_gevent_pywsgi_keepalive_chunked_and_handler_hooks():
    # Persistent connections, chunked framing when the app gives no length,
    # chunked request bodies, and the handler hooks real projects subclass
    # (handle() to count connections, log_request() to count requests).
    _check('''
from gevent.pywsgi import WSGIServer, WSGIHandler
import filament.socket as fsocket

counts = {"conns": 0, "reqs": 0}
seen = {}

class CountingHandler(WSGIHandler):
    def handle(self):
        counts["conns"] += 1
        WSGIHandler.handle(self)

    def log_request(self):
        counts["reqs"] += 1
        WSGIHandler.log_request(self)

def app(environ, start_response):
    seen["body"] = environ["wsgi.input"].read()
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"chunk-a", b"chunk-b"]          # no Content-Length -> chunked

srv = WSGIServer(("127.0.0.1", 0), app, log=None, handler_class=CountingHandler)
srv.start()
c = fsocket.create_connection(("127.0.0.1", srv.address[1]))
rfile = c.makefile("rb")

def read_response():
    head = b""
    with gevent.Timeout(5):
        while b"\\r\\n\\r\\n" not in head:
            b = rfile.read(1)
            assert b, "connection closed mid-response"
            head += b
        if b"Transfer-Encoding: chunked" in head:
            body = b""
            while True:
                size = int(rfile.readline().strip(), 16)
                if size == 0:
                    rfile.readline()
                    break
                body += rfile.read(size)
                rfile.read(2)
            return head, body
        length = 0
        for line in head.split(b"\\r\\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1])
        return head, rfile.read(length)

# Two requests down one socket: one connection, two log_request calls.
c.sendall(b"GET /one HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n")
head, body = read_response()
assert b"Transfer-Encoding: chunked" in head, head
assert body == b"chunk-achunk-b", body

# A chunked *request* body is decoded for the app.
c.sendall(b"POST /two HTTP/1.1\\r\\nHost: x\\r\\nTransfer-Encoding: chunked\\r\\n"
          b"\\r\\n3\\r\\nabc\\r\\n2\\r\\nde\\r\\n0\\r\\n\\r\\n")
head, body = read_response()
assert seen["body"] == b"abcde", seen
assert body == b"chunk-achunk-b", body
assert counts == {"conns": 1, "reqs": 2}, counts
c.close()

# stop_accepting() leaves the socket bound but takes no new connections;
# start_accepting() resumes.
srv.stop_accepting()
assert srv._accept_greenlet is None
srv.start_accepting()
c2 = fsocket.create_connection(("127.0.0.1", srv.address[1]))
c2.sendall(b"GET /three HTTP/1.1\\r\\nHost: x\\r\\nConnection: close\\r\\n\\r\\n")
assert c2.recv(12).startswith(b"HTTP/1.1 200")
c2.close()
assert counts["conns"] == 2, counts
srv.stop()
print("OK")
''')


def test_gevent_threadpool_surface():
    _check('''
from gevent.threadpool import ThreadPool

tp = ThreadPool(4)
g = tp.spawn(lambda: 21 * 2)
assert g.get() == 42
tp.join()                       # waits for outstanding tasks
assert len(tp) == 0
assert tp.size == 4
assert tp.map(lambda x: x * 10, [1, 2, 3]) == [10, 20, 30]
assert list(tp.imap(lambda a, b: a + b, [1, 2], [10, 20])) == [11, 22]
assert list(tp.imap(lambda x: x, [1, 2], maxsize=1)) == [1, 2]
assert sorted(tp.imap_unordered(lambda x: x, [3, 4])) == [3, 4]
try:
    list(tp.imap(lambda x: x, [1], bogus=1)); assert False
except TypeError:
    pass
tp.kill()                       # shut the OS-thread pool down for clean exit
print("OK")
''')


def test_gevent_uninstall():
    # uninstall() removes only entries that are still ours; idempotent.
    _check('''
import sys
import filament.gevent_compat as gc2

ok = sys.modules["gevent"]
gc2.uninstall()
assert "gevent" not in sys.modules
assert "gevent.monkey" not in sys.modules
gc2.uninstall()                     # second call: nothing left to remove
gc2.install()
import gevent as g2
assert g2 is ok
print("OK")
''')


def test_gevent_misc_surface():
    _check('''
from gevent.lock import DummySemaphore

# gevent.sleep(ref=) / idle() / __version__ marker / with_timeout.
gevent.sleep(0, ref=False)
gevent.idle()
assert gevent.__version__ == "filament-compat"
assert gevent.with_timeout(1, lambda: 3) == 3

d = DummySemaphore()
assert d.acquire() is True
assert d.acquire(blocking=False) is True
d.release()
with DummySemaphore() as ds:
    assert ds is not None
print("OK")
''')


def test_gevent_iwait_does_not_spawn_a_watcher_per_object():
    """
    iwait/joinall must learn about completions via a callback, not by parking
    a greenthread on each object.

    This is a performance contract, but it is worth pinning as a test: the
    watcher-per-object version doubled the greenthread count of every fan-out
    and cost ~2x throughput on a 20-way scatter-gather, and nothing else in the
    test suite would notice the regression coming back.
    """
    _check('''
import gc as _gc
import _filament.core
from gevent.event import Event

def count_filaments():
    return sum(1 for o in _gc.get_objects()
               if type(o) is _filament.core.Filament)

# The count has to be sampled while joinall is BLOCKED.  Sampling after it
# returns proves nothing: the watchers have finished and been reclaimed by
# then, so the regression is invisible.  So park the fan-out on an Event,
# run joinall in its own greenlet, and measure while everything is parked.
gate = Event()

def work():
    gate.wait()

N = 20

# Warm up first: lazily-built machinery must not land in the measurement.
warm = Event()
warm_gs = [gevent.spawn(warm.wait) for _ in range(5)]
warm.set()
gevent.joinall(warm_gs)
_gc.collect()

baseline = count_filaments()
gs = [gevent.spawn(work) for _ in range(N)]
gevent.sleep(0)                       # let them all reach gate.wait()
parked = count_filaments() - baseline
assert parked == N, "spawning %d greenlets created %d filaments" % (N, parked)

joiner = gevent.spawn(lambda: gevent.joinall(gs))
gevent.sleep(0)                       # let joinall attach and park
during = count_filaments() - baseline

gate.set()
joiner.join()

# N fan-out greenlets + the joiner itself.  The watcher-per-object version
# scored 2N + 1 here.
assert during <= N + 1, \\
    "joinall(%d) had %d filaments live, expected <= %d -- a watcher " \\
    "greenthread per object is back" % (N, during, N + 1)
print("OK")
''')


def test_gevent_iwait_detaches_when_abandoned():
    # The generator can be dropped without being exhausted (count=, timeout, or
    # the caller simply walking away).  A stale completion callback would keep
    # the internal queue -- and everything it references -- alive for as long
    # as the greenlet runs.
    _check('''
g = gevent.spawn(gevent.sleep, 5)

it = gevent.iwait([g])
it.close()
assert g._done_callbacks == [], g._done_callbacks

# count= short of the full set leaves the unfinished ones detached too.
fast = gevent.spawn(lambda: 1)
slow = gevent.spawn(gevent.sleep, 5)
assert len(list(gevent.iwait([fast, slow], count=1))) == 1
assert slow._done_callbacks == [], slow._done_callbacks

gevent.killall([g, slow])
print("OK")
''')


def test_gevent_iwait_mixes_raw_and_wrapped_greenlets():
    # spawn_raw returns a bare filament with no _add_done_callback, so it still
    # needs a watcher greenthread; both kinds must work in one iwait().
    _check('''
raw = gevent.spawn_raw(gevent.sleep, 0.01)
wrapped = gevent.spawn(gevent.sleep, 0.02)
done = list(gevent.iwait([raw, wrapped]))
assert len(done) == 2, done
assert set(id(x) for x in done) == set([id(raw), id(wrapped)])

# An already-finished greenlet fires its callback immediately.
g = gevent.spawn(lambda: 1)
g.join()
assert list(gevent.iwait([g])) == [g]
print("OK")
''')


def test_gevent_link_fires_before_join_returns():
    """
    A link registered before join() must have run by the time join() returns.

    gevent guarantees this structurally: its join() *is* a link, appended to
    the same ordered notification list, so everything linked earlier is
    notified first.  Wake the joiners from a separate Event before firing the
    links and join() returns with them merely queued -- which is exactly what
    a link is supposed to rule out.  Real projects route their whole
    unhandled-exception logging through link_exception(), so nothing gets
    logged at all when the ordering is wrong.
    """
    _check('''
order = []

def boom():
    raise ValueError("Boom!?")

g = gevent.spawn(boom)
g.link_exception(lambda gt: order.append("link_exception"))
g.join()
order.append("join returned")

assert order == ["link_exception", "join returned"], order

# Same contract on the value side, and for a plain link().
order2 = []
g2 = gevent.spawn(lambda: 7)
g2.link_value(lambda gt: order2.append(("value", gt.value)))
g2.link(lambda gt: order2.append("link"))
g2.join()
order2.append("join returned")
assert order2 == [("value", 7), "link", "join returned"], order2

# AsyncResult carries the same guarantee: a link registered before get()
# has run by the time get() returns.
from gevent.event import AsyncResult

order3 = []
ar = AsyncResult()
ar.link(lambda r: order3.append("link"))
gevent.spawn(lambda: ar.set(3))
assert ar.get() == 3
order3.append("get returned")
assert order3 == ["link", "get returned"], order3

print("OK")
''')


def test_gevent_greenlet_is_what_raw_getcurrent_returns():
    """
    ``greenlet.getcurrent()`` must be the running gevent Greenlet.

    Under real gevent this is structural -- ``gevent.Greenlet`` subclasses
    ``greenlet.greenlet``, so the two are literally the same object -- and
    code in the wild branches on the identity to ask "is the greenlet I am
    about to stop *me*?", taking a self-kill-safe path only when it holds.
    Answer that wrongly and the caller kills the greenlet it is running on, or
    waits on a Group containing itself; either way it deadlocks.

    filament switches on its own runtime, so the installed greenlet package
    can never see our greenthreads.  ``install()`` therefore also owns the
    top-level ``greenlet`` name; this pins the invariant that buys.
    """
    _check('''
import greenlet
from gevent.pool import Group

seen = {}
g = Group()
gl = g.spawn(lambda: seen.__setitem__("cur", greenlet.getcurrent()))
g.join()
assert seen["cur"] is gl, (seen["cur"], gl)

# gevent.getcurrent() and greenlet.getcurrent() agree, as they do in gevent.
seen2 = {}
g2 = gevent.spawn(lambda: seen2.__setitem__("cur", gevent.getcurrent()))
g2.join()
assert seen2["cur"] is g2, (seen2["cur"], g2)

# The tag does not outlive the body -- no Greenlet <-> Filament cycle left.
assert not hasattr(gl._filament, "_gevent_greenlet")

# A bare greenthread has no wrapper, so the greenthread itself comes back.
raw_seen = {}
raw = gevent.spawn_raw(lambda: raw_seen.__setitem__("cur", greenlet.getcurrent()))
gevent.sleep(0.05)
assert raw_seen["cur"] is raw, (raw_seen["cur"], raw)

# And the pattern real code relies on: a member killing itself out of its group.
g3, log = Group(), []
def suicide():
    g3.killone(greenlet.getcurrent(), block=False)
    log.append("scheduled")
g3.spawn(suicide)
g3.join(timeout=1)
assert log == ["scheduled"], log

# isinstance() against the runtime's class still works, and the module keeps
# the surface libraries version-gate on.
assert isinstance(raw, greenlet.greenlet)
assert greenlet.GreenletExit is gevent.GreenletExit
assert isinstance(greenlet.__version__, str)

print("OK")
''')


def test_gevent_hub_loop_io_watcher():
    """
    ``hub.loop.io(fd, events)`` -- the fd-watcher API pyzmq's green
    integration is built on. Without it `import zmq.green` falls through to a
    gevent<1.0 path and raises, so the shape matters: create/start/stop,
    read and write masks, ``pass_events``, and the ``cancel`` alias.
    """
    _check('''
import filament.socket as fsocket

hub = gevent.get_hub()
loop = hub.loop
a, b = fsocket.socketpair()

# READ watcher: not active until started, fires once the peer writes.
w = loop.io(a.fileno(), 1)
assert w.active is False
seen = []
w.start(lambda: seen.append(a.recv(16)))
assert w.active is True
b.sendall(b"ping")
for _ in range(50):
    gevent.sleep(0.01)
    if seen:
        break
assert seen == [b"ping"], seen

# pass_events hands the mask to the callback, as gevent does.
got = []
w.start(lambda ev: got.append(ev) or a.recv(16), pass_events=True)
b.sendall(b"x")
for _ in range(50):
    gevent.sleep(0.01)
    if got:
        break
assert got == [1], got

w.stop()
assert w.active is False
w.stop()                       # idempotent

# WRITE watcher: a fresh socketpair end is immediately writable.
w2 = loop.io(a.fileno(), 2)
wrote = []
w2.start(lambda: wrote.append(True))
for _ in range(50):
    gevent.sleep(0.01)
    if wrote:
        break
assert wrote, "write watcher never fired"
w2.close()                     # close() == stop()
assert w2.active is False

# gevent<1.0 spelling that some libraries still call.
w3 = loop.io(a.fileno(), 1)
w3.start(lambda: None)
w3.cancel()
assert w3.active is False

# run_callback is fire-and-forget on the next turn.
ran = []
loop.run_callback(lambda: ran.append(1))
gevent.sleep(0.01)
assert ran == [1]

a.close(); b.close()
print("OK")
''')


def test_gevent_signal_handler_delivers_and_cancels():
    """
    `gevent.signal_handler()` runs the handler in a greenthread. Applications
    install their SIGTERM handler this way; a missing one takes the process
    down.
    """
    _check('''
import os, signal

# Install a baseline handler first: cancel() restores whatever was there
# before, and the default SIGUSR1 action would kill this process.
baseline = []
signal.signal(signal.SIGUSR1, lambda *a: baseline.append(a))

fired = []
h = gevent.signal_handler(signal.SIGUSR1, lambda *a: fired.append(a), "tag")
assert h.ref is True
assert h.signalnum == signal.SIGUSR1

os.kill(os.getpid(), signal.SIGUSR1)
for _ in range(50):
    gevent.sleep(0.01)
    if fired:
        break
assert fired == [("tag",)], fired

# cancel() puts the previous handler back, and is idempotent.
h.cancel()
h.cancel()
fired[:] = []
os.kill(os.getpid(), signal.SIGUSR1)
gevent.sleep(0.05)
assert fired == [], fired
assert baseline, "the previous handler was not restored"
print("OK")
''')


def test_gevent_greenlet_introspection_attrs():
    """
    `args` / `kwargs` / `exc_info` / `name` / `minimal_ident` -- the
    attributes real projects reach through to identify what a greenlet is
    running for, and that logging prints.
    """
    _check('''
g = gevent.spawn(lambda a, b=None: (a, b), 1, b=2)
assert g.args == (1,), g.args
assert g.kwargs == {"b": 2}, g.kwargs
assert g.exc_info == (None, None, None), g.exc_info

# minimal_ident is assigned lazily and stable; name defaults from it.
ident = g.minimal_ident
assert isinstance(ident, int) and ident > 0
assert g.minimal_ident is ident or g.minimal_ident == ident
assert g.name == "Greenlet-%d" % ident, g.name
g.name = "worker-1"
assert g.name == "worker-1"

g.join()

# A failed greenlet reports a real (type, value, tb) triple.
bad = gevent.spawn(lambda: 1 / 0)
bad.join()
ei = bad.exc_info
assert ei[0] is ZeroDivisionError, ei
assert isinstance(ei[1], ZeroDivisionError), ei
assert ei[2] is not None
print("OK")
''')


def test_gevent_pywsgi_chunked_request_body_reads():
    """
    A chunked request body read through the WSGI ``Input`` object: sized
    read(), readline(), and read-to-EOF all have to decode the framing.
    """
    _check('''
from gevent.pywsgi import WSGIServer
import filament.socket as fsocket

seen = {}

def app(environ, start_response):
    inp = environ["wsgi.input"]
    seen["first5"] = inp.read(5)
    seen["line"] = inp.readline()
    seen["rest"] = inp.read()
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"ok"]

srv = WSGIServer(("127.0.0.1", 0), app)
srv.start()
addr = srv.address

c = fsocket.socket()
c.connect(addr)
body = b"5\\r\\nhello\\r\\n6\\r\\n world\\r\\n5\\r\\n\\ntail\\r\\n0\\r\\n\\r\\n"
c.sendall(b"POST / HTTP/1.1\\r\\nHost: x\\r\\n"
          b"Transfer-Encoding: chunked\\r\\n\\r\\n" + body)
resp = b""
while b"ok" not in resp:
    d = c.recv(4096)
    if not d:
        break
    resp += d
c.close()
srv.stop()

assert seen["first5"] == b"hello", seen
assert seen["line"] == b" world\\n", seen
assert seen["rest"] == b"tail", seen
print("OK")
''')


def test_gevent_hub_io_watcher_shutdown_paths():
    """
    The ways a watcher stops watching: the fd is closed under it, and the
    watcher is stopped while its greenthread is parked. A libev watcher just
    stops firing in both cases, so ours must too -- quietly, with no
    traceback escaping into the hub.
    """
    _check('''
import filament.socket as fsocket

loop = gevent.get_hub().loop

# fd closed under a parked watcher: the wait raises, the watcher gives up.
a, b = fsocket.socketpair()
fired = []
w = loop.io(a.fileno(), 1)
w.start(lambda: fired.append(1))
gevent.sleep(0.01)              # let it park on the fd
a.close()
b.close()
gevent.sleep(0.05)
assert fired == [], fired

# Stopped while parked: the greenthread is killed and unwinds quietly.
c, d = fsocket.socketpair()
w2 = loop.io(c.fileno(), 1)
w2.start(lambda: fired.append(2))
gevent.sleep(0.01)
w2.stop()
gevent.sleep(0.01)
d.sendall(b"z")                 # would have fired the callback if still armed
gevent.sleep(0.05)
assert fired == [], fired
c.close(); d.close()
print("OK")
''')


def test_gevent_pywsgi_degenerate_requests():
    """
    Framing paths a real client eventually produces: a stray blank line
    between keep-alive requests, a body the app never reads (both lengths and
    chunked), a malformed chunk size, and an app that forgets
    start_response.
    """
    _check('''
from gevent.pywsgi import WSGIServer
import filament.socket as fsocket

def app(environ, start_response):
    path = environ["PATH_INFO"]
    if path == "/noresp":
        return [b""]            # never calls start_response -> 500
    if path == "/read":
        # read the body inline, so a malformed or truncated chunk stream is
        # parsed while the app is running rather than during the drain
        environ["wsgi.input"].read()
    # every other path deliberately does NOT read wsgi.input: the server
    # must drain it before reusing the connection
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"ok"]

# log=None exercises the "server has no log" branch of log_request().
srv = WSGIServer(("127.0.0.1", 0), app, log=None)
srv.start()
addr = srv.address

def roundtrip(raw, expect=b"ok"):
    c = fsocket.socket()
    c.connect(addr)
    c.sendall(raw)
    buf = b""
    while expect not in buf:
        chunk = c.recv(4096)
        if not chunk:
            break
        buf += chunk
    c.close()
    return buf

# Unread Content-Length body, then a stray CRLF before the next request on
# the same connection.
c = fsocket.socket()
c.connect(addr)
c.sendall(b"POST / HTTP/1.1\\r\\nHost: x\\r\\nContent-Length: 5\\r\\n\\r\\nhello")
buf = b""
while b"ok" not in buf:
    buf += c.recv(4096)
c.sendall(b"\\r\\nGET / HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n")
buf = b""
while b"ok" not in buf:
    d = c.recv(4096)
    if not d:
        break
    buf += d
assert b"ok" in buf, buf
c.close()

# Unread chunked body: the server drains it rather than desyncing.
out = roundtrip(b"POST / HTTP/1.1\\r\\nHost: x\\r\\n"
                b"Transfer-Encoding: chunked\\r\\n\\r\\n"
                b"4\\r\\nabcd\\r\\n0\\r\\n\\r\\n")
assert b"ok" in out, out

# Malformed chunk size, read by the app: treated as end-of-body, and the
# app still gets a clean (short) read rather than an exception.
out = roundtrip(b"POST /read HTTP/1.1\\r\\nHost: x\\r\\n"
                b"Transfer-Encoding: chunked\\r\\n\\r\\nZZZZ\\r\\n")
assert b"ok" in out, out

# Truncated chunk: header promises 10 bytes, connection ends after 3.
c = fsocket.socket()
c.connect(addr)
c.sendall(b"POST /read HTTP/1.1\\r\\nHost: x\\r\\n"
          b"Transfer-Encoding: chunked\\r\\n\\r\\na\\r\\nabc")
c.shutdown(fsocket.SHUT_WR)
buf = b""
while True:
    d = c.recv(4096)
    if not d:
        break
    buf += d
c.close()
assert b"ok" in buf, buf

# App never calls start_response.
out = roundtrip(b"GET /noresp HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n", expect=b"500")
assert b"500" in out, out

srv.stop()
print("OK")
''')
