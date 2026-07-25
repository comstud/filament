"""Cooperative-ish replacement for the ``subprocess`` module.

The ``call``/``check_call``/``check_output`` wrappers follow the canonical
CPython ``subprocess`` idiom (Python Software Foundation License Version 2;
see ``LICENSE.PSF``).

Greening ``subprocess`` in full is a large undertaking; this module implements
the parts that otherwise block the whole process:

* ``Popen.wait`` polls the child cooperatively (non-blocking ``poll`` + a short
  ``filament.sleep``) instead of blocking the OS thread in ``os.waitpid``.
* ``Popen.communicate`` drains the child's stdout/stderr (and feeds stdin) using
  filament greenthreads and cooperative fd waits, so it neither deadlocks on
  full pipe buffers nor blocks the scheduler.
* ``call`` / ``check_call`` / ``run`` / ``check_output`` are thin wrappers built
  on the cooperative ``Popen`` above.

Everything else (constants such as ``PIPE``/``STDOUT``, exception types,
``CalledProcessError``, ``TimeoutExpired``, ...) is copied from the stdlib.

Limitations / not done (documented):
* Only the POSIX path is greened cooperatively (``fd_wait_*`` needs real fds).
  On platforms without it we fall back to the stdlib behaviour.
* ``communicate`` reads in binary and honours ``text``/``universal_newlines``
  only for decoding at the end; exotic encodings/newline translation edge cases
  defer to bytes.
"""

import os as _os_module
import sys

import filament as _fil
from filament import _util as _fil_util
from filament import patcher as _fil_patcher

try:
    import _filament.io as _fil_io
except ImportError:  # pragma: no cover - C ext not built
    _fil_io = None

__filament__ = {'patch': 'subprocess'}

# Pristine stdlib subprocess, source of the base Popen and all constants.
_orig_subprocess = _fil_patcher.get_original('subprocess')

_PY3 = sys.version_info[0] >= 3

# A small poll interval for wait(): short enough to feel responsive, long enough
# to avoid busy-spinning the scheduler.
_WAIT_POLL_INTERVAL = 0.005


class Popen(_orig_subprocess.Popen):
    """A ``subprocess.Popen`` whose blocking calls cooperate with filaments."""

    def wait(self, timeout=None):
        """Wait for the child to exit, yielding to other greenthreads.

        Rather than blocking in ``os.waitpid``, we repeatedly call the
        non-blocking ``poll()`` and ``filament.sleep`` in between, so the
        scheduler keeps running other greenthreads while we wait.
        """
        if self.returncode is not None:
            return self.returncode

        deadline = None
        if timeout is not None:
            deadline = _monotonic() + timeout

        while True:
            rc = self.poll()  # stdlib poll() is non-blocking (uses WNOHANG)
            if rc is not None:
                return rc
            if deadline is not None and _monotonic() >= deadline:
                # Match the stdlib's timeout contract.
                raise _orig_subprocess.TimeoutExpired(self.args, timeout)
            _fil.sleep(_WAIT_POLL_INTERVAL)

    def communicate(self, input=None, timeout=None):
        """Cooperative ``communicate``.

        Spawns greenthreads to drain stdout and stderr concurrently while
        (optionally) feeding ``input`` to stdin, then waits for the child.  This
        avoids the classic pipe-buffer deadlock without blocking the scheduler.
        """
        # If we can't do cooperative fd IO, or there are no pipes to service,
        # fall back to the (still cooperative-wait) stdlib implementation.
        if _fil_io is None:
            return super(Popen, self).communicate(input=input, timeout=timeout)

        stdout_data = [b'']
        stderr_data = [b'']
        helpers = []

        def _drain(fileobj, sink):
            fd = fileobj.fileno()
            chunks = []
            while True:
                try:
                    _fil_io.fd_wait_read_ready(fd)
                    chunk = _os_module.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            sink[0] = b''.join(chunks)
            try:
                fileobj.close()
            except Exception:
                pass

        def _feed(fileobj, data):
            try:
                mv = memoryview(data)
                offset = 0
                fd = fileobj.fileno()
                while offset < len(mv):
                    _fil_io.fd_wait_write_ready(fd)
                    offset += _os_module.write(fd, mv[offset:])
            except OSError:
                pass
            finally:
                try:
                    fileobj.close()
                except Exception:
                    pass

        if self.stdin is not None:
            if input is not None:
                data = input
                if isinstance(data, str) and _PY3:
                    data = data.encode()
                helpers.append(_fil.spawn(_feed, self.stdin, data))
            else:
                try:
                    self.stdin.close()
                except Exception:
                    pass
        if self.stdout is not None:
            helpers.append(_fil.spawn(_drain, self.stdout, stdout_data))
        if self.stderr is not None:
            helpers.append(_fil.spawn(_drain, self.stderr, stderr_data))

        for h in helpers:
            h.join()

        self.wait(timeout=timeout)

        out = stdout_data[0] if self.stdout is not None else None
        err = stderr_data[0] if self.stderr is not None else None

        # Honour text mode for the returned data.
        if self._wants_text():
            if out is not None:
                out = out.decode()
            if err is not None:
                err = err.decode()
        return (out, err)

    def _wants_text(self):
        # ``text``/``universal_newlines``/``encoding`` all imply str output.
        return bool(getattr(self, 'text_mode', False)
                    or getattr(self, 'universal_newlines', False)
                    or getattr(self, 'encoding', None))


def _monotonic():
    # Prefer a monotonic clock; fall back for very old Pythons.
    import time as _t
    return getattr(_t, 'monotonic', _t.time)()


# ---------------------------------------------------------------------------
# Thin cooperative wrappers built on the green Popen.
# ---------------------------------------------------------------------------

def call(*popenargs, **kwargs):
    """Run a command, wait for it (cooperatively), return the return code."""
    timeout = kwargs.pop('timeout', None)
    with Popen(*popenargs, **kwargs) as p:
        try:
            return p.wait(timeout=timeout)
        except Exception:
            p.kill()
            p.wait()
            raise


def check_call(*popenargs, **kwargs):
    """Like :func:`call` but raise ``CalledProcessError`` on non-zero exit."""
    retcode = call(*popenargs, **kwargs)
    if retcode:
        cmd = kwargs.get('args') or (popenargs[0] if popenargs else None)
        raise _orig_subprocess.CalledProcessError(retcode, cmd)
    return 0


def check_output(*popenargs, **kwargs):
    """Run a command and return its stdout (cooperatively)."""
    if 'stdout' in kwargs:
        raise ValueError('stdout argument not allowed, it will be overridden.')
    kwargs['stdout'] = _orig_subprocess.PIPE
    timeout = kwargs.pop('timeout', None)
    inp = kwargs.pop('input', None)
    with Popen(*popenargs, **kwargs) as p:
        try:
            out, _err = p.communicate(input=inp, timeout=timeout)
        except Exception:
            p.kill()
            p.wait()
            raise
        retcode = p.poll()
        if retcode:
            cmd = kwargs.get('args') or (popenargs[0] if popenargs else None)
            raise _orig_subprocess.CalledProcessError(retcode, cmd, output=out)
    return out


def run(*popenargs, **kwargs):
    """A cooperative ``subprocess.run`` (subset of the stdlib signature)."""
    inp = kwargs.pop('input', None)
    timeout = kwargs.pop('timeout', None)
    check = kwargs.pop('check', False)
    capture = kwargs.pop('capture_output', False)
    if capture:
        kwargs.setdefault('stdout', _orig_subprocess.PIPE)
        kwargs.setdefault('stderr', _orig_subprocess.PIPE)
    with Popen(*popenargs, **kwargs) as p:
        try:
            out, err = p.communicate(input=inp, timeout=timeout)
        except Exception:
            p.kill()
            p.wait()
            raise
        retcode = p.poll()
    if check and retcode:
        cmd = kwargs.get('args') or (popenargs[0] if popenargs else None)
        raise _orig_subprocess.CalledProcessError(retcode, cmd, output=out,
                                                  stderr=err)
    # ``CompletedProcess`` exists on Py3.5+.
    cp = getattr(_orig_subprocess, 'CompletedProcess', None)
    if cp is not None:
        cmd = kwargs.get('args') or (popenargs[0] if popenargs else None)
        return cp(cmd, retcode, out, err)
    return retcode


# Copy across everything we did not override: PIPE, STDOUT, DEVNULL,
# CalledProcessError, TimeoutExpired, SubprocessError, list2cmdline, etc.
_fil_util.copy_globals(_orig_subprocess, globals())
