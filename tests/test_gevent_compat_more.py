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
    c = fsocket.create_connection(("127.0.0.1", srv.address[1]))
    c.sendall(req)
    resp = b""
    with gevent.Timeout(5):
        while True:
            chunk = c.recv(4096)
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
