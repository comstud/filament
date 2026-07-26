# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Shared helpers for the filament test suite.

Written to run on Python 2.7 and Python 3.5 - 3.13:
  * no f-strings
  * ``from __future__ import`` where syntax matters
  * a hand-rolled subprocess timeout (py2.7's Popen has no ``timeout=``)

The most important helper here is :func:`run_py`.  Several filament features
(the monkey-patcher, the gevent/eventlet ``install()`` shims, ``patch_thread``)
mutate *process-global* state (``sys.modules``, ``logging._lock``, ...).  Running
those scenarios in-process would pollute every subsequent test.  We therefore
run each such scenario in a **fresh subprocess** and assert on its exit status
and output.  This also gives us a hard wall-clock guard: a deadlock becomes a
subprocess timeout -> a clean test failure instead of hanging the whole run.
"""

from __future__ import absolute_import

import os
import subprocess
import sys
import threading

# Repo root == parent directory of this ``tests`` package.  Both ``filament``
# (pure Python) and ``_filament`` (the compiled extension) live directly under
# it, so putting it on PYTHONPATH is all a child process needs.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PY3 = sys.version_info[0] >= 3


class ProcResult(object):
    """Result of :func:`run_py`."""

    def __init__(self, returncode, stdout, stderr, timed_out):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out

    def ok(self):
        return (not self.timed_out) and self.returncode == 0

    def __repr__(self):
        return ("ProcResult(rc=%r, timed_out=%r)\n--- stdout ---\n%s\n"
                "--- stderr ---\n%s" % (self.returncode, self.timed_out,
                                        self.stdout, self.stderr))


def run_py(body, timeout=30, extra_env=None):
    """
    Run ``body`` (a Python program, as a string) in a fresh interpreter.

    Returns a :class:`ProcResult`.  The child has this repo on its PYTHONPATH so
    ``import filament`` / ``import _filament`` work.  A watchdog kills the child
    if it runs longer than ``timeout`` seconds (a deadlock => ``timed_out``).
    """
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (REPO_ROOT + os.pathsep + existing) if existing \
        else REPO_ROOT
    if extra_env:
        env.update(extra_env)

    proc = subprocess.Popen(
        [sys.executable, "-c", body],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        cwd=REPO_ROOT,
    )

    state = {"timed_out": False}

    def _kill():
        state["timed_out"] = True
        try:
            proc.kill()
        except Exception:
            pass

    timer = threading.Timer(timeout, _kill)
    timer.start()
    try:
        out, err = proc.communicate()
    finally:
        timer.cancel()

    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    return ProcResult(proc.returncode, out, err, state["timed_out"])


def make_self_signed_cert():
    """
    Generate a throwaway self-signed cert/key pair in a temp dir.

    Returns ``(certfile, keyfile)`` or ``(None, None)`` if no generator is
    available (so callers can skip gracefully).  Tries the ``ssl``/``trustme``
    route first, falling back to the ``openssl`` CLI.
    """
    import tempfile
    d = tempfile.mkdtemp(prefix="filament-cert-")
    certfile = os.path.join(d, "cert.pem")
    keyfile = os.path.join(d, "key.pem")

    # Fast path: the openssl CLI is almost always present.
    try:
        rc = subprocess.call(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048",
             "-keyout", keyfile, "-out", certfile, "-days", "1", "-nodes",
             "-subj", "/CN=localhost"],
            stdout=open(os.devnull, "wb"), stderr=open(os.devnull, "wb"))
        if rc == 0 and os.path.exists(certfile) and os.path.exists(keyfile):
            return certfile, keyfile
    except (OSError, IOError):
        pass

    return None, None


# Body preamble that a subprocess script can prepend: forces line-buffered
# stdout and makes a clean, hang-proof exit at the end via os._exit.
# NB for C-coverage runs: os._exit skips the gcov atexit flush, so scenarios
# that end here (and test_cross_thread_137's hard exits) undercount C lines a
# little; __gcov_dump is not dlsym-reachable (libgcov links static/hidden), so
# there is no clean in-child flush -- treat gcov totals as a floor.
PREAMBLE = (
    "import sys, os\n"
    "def _done(msg='OK'):\n"
    "    sys.stdout.write(msg + '\\n')\n"
    "    sys.stdout.flush()\n"
    "    os._exit(0)\n"
)
