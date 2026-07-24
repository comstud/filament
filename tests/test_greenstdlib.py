# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Green stdlib tests (used directly, WITHOUT monkey-patching global sys.modules):

  * filament.time.sleep(0) is cooperative
  * filament.threading.Thread runs cooperatively; local() is greenthread-local
  * filament.os.read/write cooperate on a pipe
  * filament.subprocess runs a child cooperatively (other greenthreads progress
    while we wait for it)

Monkey-patched behavior (patch_all making ``import time`` return the green one)
is covered separately, in subprocesses, in test_patcher.py.
"""

from __future__ import absolute_import

import os as _real_os
import sys

import pytest

import filament
from filament import time as ftime
from filament import threading as fthreading
from filament import os as fos
from filament import subprocess as fsubprocess


def run(fn):
    return filament.spawn(fn).wait()


# --------------------------------------------------------------------------- #
# time
# --------------------------------------------------------------------------- #

def test_time_sleep0_is_cooperative():
    def body():
        counter = [0]

        def busy():
            for _ in range(100):
                counter[0] += 1
                ftime.sleep(0)

        g = filament.spawn(busy)
        ftime.sleep(0.01)          # cooperative: lets busy run
        progressed = counter[0]
        g.wait()
        return progressed

    assert run(body) > 0


def test_time_has_time_function():
    # The green time module still exposes the normal clock functions.
    assert isinstance(ftime.time(), float)


# --------------------------------------------------------------------------- #
# threading (cooperative Thread + greenthread-local)
# --------------------------------------------------------------------------- #

def test_cooperative_thread_runs():
    def body():
        out = []
        t = fthreading.Thread(target=lambda: out.append("ran"))
        t.start()
        t.join()
        return out

    assert run(body) == ["ran"]


def test_cooperative_thread_with_args():
    def body():
        out = []

        def work(a, b):
            out.append(a + b)

        t = fthreading.Thread(target=work, args=(2,), kwargs={"b": 3})
        t.start()
        t.join()
        return out

    assert run(body) == [5]


def test_greenthread_local_isolation():
    # Two greenthreads must NOT see each other's local() value.
    def body():
        loc = fthreading.local()
        loc.value = "main"
        seen = []

        def worker(v):
            loc.value = v
            filament.sleep(0.001)  # yield; another worker runs in between
            seen.append(loc.value)

        gs = [filament.spawn(worker, i) for i in range(5)]
        filament.joinall(gs)
        # Each worker sees its OWN value despite interleaving.
        return sorted(seen), loc.value

    seen, main_value = run(body)
    assert seen == [0, 1, 2, 3, 4]
    assert main_value == "main"     # main greenthread's value untouched


# --------------------------------------------------------------------------- #
# os.read / os.write cooperate on a pipe
# --------------------------------------------------------------------------- #

def test_os_read_write_pipe():
    def body():
        r, w = _real_os.pipe()
        got = []

        def reader():
            got.append(fos.read(r, 10))

        g = filament.spawn(reader)
        filament.sleep(0.005)      # reader parks waiting for data
        fos.write(w, b"hi")
        g.wait()
        _real_os.close(r)
        _real_os.close(w)
        return got

    assert run(body) == [b"hi"]


# --------------------------------------------------------------------------- #
# subprocess cooperative wait/communicate
# --------------------------------------------------------------------------- #

def test_subprocess_communicate_output():
    def body():
        p = fsubprocess.Popen(
            [sys.executable, "-c", "print('child-output')"],
            stdout=fsubprocess.PIPE)
        out, err = p.communicate()
        return out.strip(), p.returncode

    out, rc = run(body)
    assert out == b"child-output"
    assert rc == 0


def test_subprocess_wait_lets_others_progress():
    def body():
        counter = [0]

        def busy():
            for _ in range(200):
                counter[0] += 1
                filament.sleep(0.001)

        g = filament.spawn(busy)
        p = fsubprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.15)"])
        p.wait()
        progressed = counter[0]
        g.wait()
        return p.returncode, progressed

    rc, progressed = run(body)
    assert rc == 0
    assert progressed > 0          # scheduler kept running during the wait


def test_subprocess_nonzero_returncode():
    def body():
        p = fsubprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
        p.wait()
        return p.returncode

    assert run(body) == 3
