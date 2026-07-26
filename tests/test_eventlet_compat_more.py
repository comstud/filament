# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
eventlet drop-in shim tests -- coverage-gap additions.

Same pattern as test_eventlet_compat.py: each scenario runs in a fresh
subprocess doing the documented ``import filament.eventlet_compat as ec;
ec.install(); import eventlet`` dance, because install() mutates sys.modules
process-globally.  These tests target the previously-uncovered paths:
selective/no-op monkey_patch, import_patched, listen/connect extras, wrap_ssl,
serve + StopServe kill, GreenThread link/unlink/kill/cancel, the hubs surface
and BoundedSemaphore's context manager.
"""

from __future__ import absolute_import

import pytest

from tests._helpers import run_py, make_self_signed_cert

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


def test_eventlet_monkey_patch_default_and_noop():
    # No subsystem flags + all=False -> nothing patched; a later plain
    # monkey_patch() patches everything.  is_monkey_patched accepts both a
    # string and a module object.
    _check('''
import socket as real_socket_mod

eventlet.monkey_patch(all=False)
assert eventlet.is_monkey_patched("socket") is False

eventlet.monkey_patch()
import socket
assert "filament" in socket.__file__, socket.__file__
assert eventlet.is_monkey_patched("socket") is True
# Module-object form: reads __name__ off the (pre-patch) module object.
assert eventlet.is_monkey_patched(real_socket_mod) is True
print("OK")
''')


def test_eventlet_monkey_patch_selective():
    # Naming ANY subsystem switches to patch-only-the-named-ones mode; name
    # them all so every selective branch runs.  MySQLdb/builtins are documented
    # accepted-and-ignored no-ops.
    _check('''
eventlet.monkey_patch(os=True, select=True, socket=True, thread=True,
                      time=True, subprocess=True, ssl=True,
                      MySQLdb=True, builtins=True)
import socket, time, select, ssl
assert "filament" in socket.__file__, socket.__file__
assert "filament" in time.__file__, time.__file__
assert "filament" in select.__file__, select.__file__
assert "filament" in ssl.__file__, ssl.__file__
assert eventlet.is_monkey_patched("socket") is True
assert eventlet.is_monkey_patched("time") is True
print("OK")
''')


def test_eventlet_monkey_patch_single_subsystem():
    # Naming ONE subsystem must patch only that one.
    _check('''
eventlet.monkey_patch(socket=True)
import socket, time
assert "filament" in socket.__file__, socket.__file__
# The unpatched stdlib time module is the C builtin (no __file__).
assert "filament" not in getattr(time, "__file__", "")
assert eventlet.is_monkey_patched("time") is False
print("OK")
''')


def test_eventlet_import_patched():
    # import_patched imports a FRESH copy of the target bound to the green
    # stdlib, without touching the global module table.
    _check('''
import sys
import ftplib as real_ftplib
import socket as real_socket

patched = eventlet.import_patched("ftplib", "ignored-extra",
                                  also_ignored=True)
# The patched copy's stdlib imports resolved to filament's green modules.
assert "filament" in patched.socket.__file__, patched.socket.__file__
assert patched is not real_ftplib

# The global module table was fully restored.
assert sys.modules["ftplib"] is real_ftplib
import socket
assert socket is real_socket
assert "filament" not in socket.__file__

# The real ftplib still points at the real socket.
assert real_ftplib.socket is real_socket
print("OK")
''')


def test_eventlet_listen_connect_extras():
    # listen() with reuse_port + explicit backlog; connect() with bind=.
    _check('''
import filament

server_sock = eventlet.listen(("127.0.0.1", 0), backlog=10,
                              reuse_addr=True, reuse_port=True)
addr = server_sock.getsockname()

def srv():
    conn, _ = server_sock.accept()
    conn.sendall(conn.recv(100))
    conn.close()

filament.spawn_n(srv)
c = eventlet.connect(addr, bind=("127.0.0.1", 0))
assert c.getsockname()[0] == "127.0.0.1"
c.sendall(b"bound-echo")
got = c.recv(100)
c.close()
server_sock.close()
assert got == b"bound-echo", got
print("OK")
''')


def test_eventlet_serve_stopserve_kill():
    # serve() accept loop: a handled connection (wrapper closes the client in
    # its finally), then killing the serving greenthread with StopServe makes
    # serve() return cleanly (the documented stop path).
    _check('''
server_sock = eventlet.listen(("127.0.0.1", 0))
addr = server_sock.getsockname()

def handle(client, client_addr):
    while True:
        data = client.recv(1024)
        if not data:
            break
        client.sendall(data)

server_gt = eventlet.spawn(eventlet.serve, server_sock, handle)

client = eventlet.connect(addr)
client.sendall(b"serve-echo")
got = b""
while len(got) < len(b"serve-echo"):
    got += client.recv(100)
client.close()
assert got == b"serve-echo", got
eventlet.sleep(0.05)

# Throw StopServe into the accept loop -> serve() returns None.
server_gt.kill(eventlet.StopServe)
assert server_gt.wait() is None
assert server_gt.dead is True
print("OK")
''')


def test_eventlet_wrap_ssl_echo():
    certfile, keyfile = make_self_signed_cert()
    if certfile is None:
        pytest.skip("no self-signed cert generator available")
    _check('''
ls = eventlet.listen(("127.0.0.1", 0))
addr = ls.getsockname()

def server():
    conn, _ = ls.accept()
    sconn = eventlet.wrap_ssl(conn, server_side=True,
                              certfile=%r, keyfile=%r)
    sconn.sendall(sconn.recv(100))
    sconn.close()
    ls.close()

eventlet.spawn_n(server)
c = eventlet.connect(addr)
sc = eventlet.wrap_ssl(c)
sc.sendall(b"ssl-echo")
got = sc.recv(100)
sc.close()
assert got == b"ssl-echo", got
print("OK")
''' % (certfile, keyfile))


def test_eventlet_greenthread_kill_links_cancel():
    _check('''
from eventlet import greenthread

# getcurrent
assert greenthread.getcurrent() is not None

# Killing a running greenthread: wait() returns the GreenletExit VALUE.
gt = eventlet.spawn(eventlet.sleep, 5)
eventlet.sleep(0.01)
gt.kill()
res = gt.wait()
assert isinstance(res, eventlet.GreenletExit), res
assert gt.dead is True

# kill with explicit throw args -> the exception is the OUTCOME.
gt2 = eventlet.spawn(eventlet.sleep, 5)
eventlet.sleep(0.01)
greenthread.kill(gt2, ValueError("boom"))
raised = False
try:
    gt2.wait()
except ValueError as e:
    raised = "boom" in str(e)
assert raised

# link before completion: callback gets (greenthread, *args).
seen = []
gt3 = eventlet.spawn(lambda: 7)
gt3.link(lambda g, extra: seen.append((g.wait(), extra)), "x")
gt3.wait()
eventlet.sleep(0.05)
assert seen == [(7, "x")], seen

# link after completion fires immediately (scheduled).
seen2 = []
gt3.link(lambda g: seen2.append(g.wait()))
eventlet.sleep(0.05)
assert seen2 == [7], seen2

# unlink removes a pending callback.
seen3 = []
def cb(g):
    seen3.append(1)
gt4 = eventlet.spawn(eventlet.sleep, 0.05)
gt4.link(cb)
gt4.unlink(cb)
gt4.wait()
eventlet.sleep(0.05)
assert seen3 == [], seen3

# cancel (method + module fn) is treated as kill.
gt5 = eventlet.spawn(eventlet.sleep, 5)
eventlet.sleep(0.01)
gt5.cancel()
assert isinstance(gt5.wait(), eventlet.GreenletExit)
gt6 = eventlet.spawn(eventlet.sleep, 5)
eventlet.sleep(0.01)
greenthread.cancel(gt6)
assert isinstance(gt6.wait(), eventlet.GreenletExit)

# __getattr__ forwards unknown attributes to the raw filament greenthread.
gt7 = eventlet.spawn(lambda: 1)
gt7.wait()
_ = gt7.parent          # Filament attribute, not on GreenThread
raised = False
try:
    gt7.no_such_attribute_xyz
except AttributeError:
    raised = True
assert raised

# spawn_after_local behaves like spawn_after; a pending one can be cancelled.
box = []
h = eventlet.spawn_after_local(0.02, lambda: box.append(1))
h.wait()
assert box == [1], box
h2 = eventlet.spawn_after(5, lambda: box.append(2))
h2.cancel()
eventlet.sleep(0.05)
assert box == [1], box
print("OK")
''')


def test_eventlet_hubs_surface():
    _check('''
import filament
from eventlet import hubs
from eventlet.green import socket as gsocket

hub = hubs.get_hub()
assert hubs.get_hub() is hub            # singleton
assert hub.greenlet is not None
hub.switch()                            # yields to the scheduler

box = []
handle = hub.schedule_call_global(0.01, lambda v: box.append(v), "sg")
h2 = hub.schedule_call_local(5, lambda: box.append("never"))
h2.cancel()
eventlet.sleep(0.1)
assert box == ["sg"], box

assert hubs.use_hub("whatever") is None     # accepted no-op

a, b = gsocket.socketpair()

# write-ready trampoline: fd object and raw int forms.
hubs.trampoline(b, write=True, timeout=1.0)
hubs.trampoline(b.fileno(), write=True, timeout=1.0)

# neither read nor write requested -> immediate None.
assert hubs.trampoline(b.fileno()) is None

# read-ready trampoline unblocks when the peer writes.
def writer():
    filament.sleep(0.02)
    a.sendall(b"z")
filament.spawn_n(writer)
hubs.trampoline(b, read=True, timeout=1.0)
assert b.recv(1) == b"z"

# read timeout raises filament's Timeout by default.
timed_out = False
try:
    hubs.trampoline(b, read=True, timeout=0.05)
except BaseException as e:
    timed_out = isinstance(e, filament.Timeout)
assert timed_out
a.close(); b.close()
print("OK")
''')


def test_eventlet_uninstall():
    # uninstall() removes only entries that are still ours; idempotent.
    _check('''
import sys
import filament.eventlet_compat as ec2

assert sys.modules["eventlet"] is ec2.main
ec2.uninstall()
assert "eventlet" not in sys.modules
assert "eventlet.green.socket" not in sys.modules
ec2.uninstall()                     # second call: nothing left to remove
ec2.install()
import eventlet as e2
assert e2 is ec2.main
print("OK")
''')


def test_eventlet_bounded_semaphore_more():
    _check('''
from eventlet.semaphore import BoundedSemaphore

bs = BoundedSemaphore(1)
with bs:
    # held: non-blocking and timed acquires fail without raising.
    assert bs.acquire(blocking=False) is False
    assert bs.acquire(timeout=0.02) is False
# released by the context manager; a fresh acquire works.
assert bs.acquire() is True
bs.release()
raised = False
try:
    bs.release()
except ValueError:
    raised = True
assert raised
print("OK")
''')
