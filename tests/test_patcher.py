# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Monkey-patcher tests.

Every scenario runs in a FRESH subprocess (via ``run_py``) because
``patch_all`` / ``patch_thread`` mutate process-global state (``sys.modules``,
``logging._lock``, ...).  Running them in-process would irreversibly pollute
every later test.  Each child asserts with plain ``assert`` and prints ``OK``;
the test asserts a clean exit and the ``OK`` marker.
"""

from __future__ import absolute_import

from tests._helpers import run_py


def _check(script, timeout=25):
    res = run_py(script, timeout=timeout)
    assert not res.timed_out, "subprocess timed out\n" + repr(res)
    assert res.returncode == 0, repr(res)
    assert "OK" in res.stdout, repr(res)
    return res


def test_patch_all_swaps_modules():
    _check('''
import sys
import filament.patcher as patcher
patcher.patch_all()

import socket, time, select, os
# threading module name (_thread on py3, thread on py2)
_PY3 = sys.version_info[0] >= 3
import threading

# The stdlib names now resolve to filament green modules.
assert "filament" in socket.__file__, socket.__file__
assert "filament" in time.__file__, time.__file__
assert "filament" in select.__file__, select.__file__
assert getattr(threading, "__filament__", None), "threading not greened"
# os is item-level patched (read/write/fdopen swapped), not whole-module.
assert patcher.is_module_patched("socket")
assert patcher.is_module_patched("time")
print("OK")
''')


def test_patch_all_idempotent():
    _check('''
import filament.patcher as patcher
patcher.patch_all()
import socket as s1
patcher.patch_all()           # second call must be a harmless no-op
import socket as s2
assert s1 is s2
assert "filament" in s1.__file__
print("OK")
''')


def test_get_original_returns_real():
    _check('''
import filament.patcher as patcher
real_socket_mod = patcher.get_original("socket")
real_socket_cls = patcher.get_original("socket", "socket")
patcher.patch_all()
import socket as green
# The green module is now installed, but get_original still hands back the real
# one captured at patch time.
assert "filament" not in real_socket_mod.__file__, real_socket_mod.__file__
assert "filament" in green.__file__
# get_original after patching still returns the pristine module/attr.
again = patcher.get_original("socket")
assert again is real_socket_mod
assert patcher.get_original("socket", "socket") is real_socket_cls
print("OK")
''')


def test_patch_thread_local_is_greenthread_local():
    _check('''
import filament.patcher as patcher
patcher.patch_thread()
import threading
import filament

loc = threading.local()
loc.value = "main"
seen = []

def worker(v):
    loc.value = v
    filament.sleep(0.001)
    seen.append(loc.value)

gs = [filament.spawn(worker, i) for i in range(5)]
filament.joinall(gs)
assert sorted(seen) == [0, 1, 2, 3, 4], seen
assert loc.value == "main", loc.value   # per-greenthread isolation
print("OK")
''')


def test_patch_thread_swaps_logging_lock():
    _check('''
import filament.patcher as patcher
import logging

# Before patching, logging._lock is a native (C) thread lock.
before = logging._lock
before_type = type(before).__module__ + "." + type(before).__name__

patcher.patch_thread(logging=True, existing_locks=True)

after = logging._lock
after_type = type(after).__module__ + "." + type(after).__name__

# The module-level logging lock must now be a filament cooperative RLock.
assert "filament" in after_type or "_filament" in after_type, after_type
assert after is not before
# logging's own threading reference was repointed at the green threading module.
assert getattr(logging.threading, "__filament__", None), "logging.threading not green"
print("OK")
''')


def test_patch_thread_logging_handler_locks():
    _check('''
import filament.patcher as patcher
import logging

# Create a handler BEFORE patching so its lock is a native one.
handler = logging.StreamHandler()
logging.getLogger("pre_existing").addHandler(handler)

patcher.patch_thread(logging=True, existing_locks=True)

lock_type = type(handler.lock).__module__ + "." + type(handler.lock).__name__
assert "filament" in lock_type, lock_type
print("OK")
''')


def test_is_module_patched_false_before():
    _check('''
import filament.patcher as patcher
assert patcher.is_module_patched("socket") is False
patcher.patch_socket()
assert patcher.is_module_patched("socket") is True
print("OK")
''')


def test_granular_patch_time_only():
    _check('''
import filament.patcher as patcher
patcher.patch_time()
import time
assert "filament" in time.__file__
import socket
# socket was NOT patched (only time was).
assert "filament" not in socket.__file__
print("OK")
''')


def test_patched_socket_echo_works():
    # End-to-end: after patch_all, ordinary blocking-looking socket code runs
    # cooperatively under filament.
    _check('''
import filament.patcher as patcher
patcher.patch_all()
import socket
import filament

def server(q):
    ls = socket.socket()
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)
    q.append(ls.getsockname())
    conn, _ = ls.accept()
    data = conn.recv(100)
    conn.sendall(data)
    conn.close()
    ls.close()

q = []
filament.spawn_n(lambda: server(q))
while not q:
    filament.sleep(0)
addr = q[0]
c = socket.create_connection(addr)
c.sendall(b"patched-echo")
got = b""
while len(got) < len(b"patched-echo"):
    got += c.recv(100)
c.close()
assert got == b"patched-echo", got
print("OK")
''', timeout=25)
