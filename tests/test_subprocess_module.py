# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Coverage-gap tests for ``filament.subprocess``:

  * ``Popen.wait``: exit-code return, already-exited short-circuit, timeout
    raising ``TimeoutExpired``, and cooperativeness (a ticker greenthread keeps
    running while we wait).
  * ``Popen.communicate``: bytes and str input (str is encoded), stdin closed
    when no input is given, stderr draining, text-mode decoding, the
    broken-pipe feed path, and the stdlib fallback when ``_filament.io`` is
    unavailable.
  * The module-level wrappers: ``call`` / ``check_call`` / ``check_output`` /
    ``run``, including their timeout/kill exception paths and the
    ``CalledProcessError`` variants (with output / with stderr).

All children are short-lived ``sys.executable -c`` one-liners; every wait is
bounded (sleeping children are killed after the expected timeout fires).
"""

from __future__ import absolute_import

import sys

import pytest

import filament
from filament import subprocess as fsubprocess


PY = sys.executable


def run(fn):
    return filament.spawn(fn).wait()


def _cmd(code):
    return [PY, '-c', code]


# A child that closes its stdout/stderr pipes immediately and then sleeps
# "forever"; used to force communicate()'s drain helpers to finish (EOF) while
# wait() times out.  It is killed by the timeout paths under test.
_CLOSE_OUTPUTS_AND_HANG = ('import os, time; os.close(1); os.close(2); '
                           'time.sleep(30)')


# --------------------------------------------------------------------------- #
# Popen.wait
# --------------------------------------------------------------------------- #

def test_wait_returns_exit_code():
    def body():
        p = fsubprocess.Popen(_cmd('import sys; sys.exit(3)'))
        return p.wait()

    assert run(body) == 3


def test_wait_short_circuits_when_already_exited():
    def body():
        p = fsubprocess.Popen(_cmd('import sys; sys.exit(7)'))
        first = p.wait()
        # returncode is set now; a second wait() must return immediately.
        second = p.wait()
        return (first, second, p.returncode)

    assert run(body) == (7, 7, 7)


def test_wait_timeout_raises_timeoutexpired():
    def body():
        p = fsubprocess.Popen(_cmd('import time; time.sleep(30)'))
        try:
            with pytest.raises(fsubprocess.TimeoutExpired):
                p.wait(timeout=0.1)
            # Child is still running; it survived the timed-out wait.
            assert p.poll() is None
        finally:
            p.kill()
            p.wait()
        return 'ok'

    assert run(body) == 'ok'


def test_wait_is_cooperative():
    # Other greenthreads must keep running while we wait on a child.
    def body():
        ticks = [0]
        stop = [False]

        def ticker():
            while not stop[0]:
                ticks[0] += 1
                filament.sleep(0.005)

        t = filament.spawn(ticker)
        p = fsubprocess.Popen(_cmd('import time; time.sleep(0.25)'))
        rc = p.wait()
        stop[0] = True
        t.wait()
        return (rc, ticks[0])

    rc, ticks = run(body)
    assert rc == 0
    assert ticks > 5


# --------------------------------------------------------------------------- #
# Popen.communicate
# --------------------------------------------------------------------------- #

_UPPER_CHILD = ('import sys; '
                'i = getattr(sys.stdin, "buffer", sys.stdin); '
                'o = getattr(sys.stdout, "buffer", sys.stdout); '
                'o.write(i.read().upper())')


def test_communicate_bytes_input():
    def body():
        p = fsubprocess.Popen(_cmd(_UPPER_CHILD),
                              stdin=fsubprocess.PIPE,
                              stdout=fsubprocess.PIPE)
        out, err = p.communicate(input=b'hello')
        return (out, err, p.returncode)

    assert run(body) == (b'HELLO', None, 0)


def test_communicate_str_input_is_encoded():
    # A str input in binary mode goes through the encode() path.
    def body():
        p = fsubprocess.Popen(_cmd(_UPPER_CHILD),
                              stdin=fsubprocess.PIPE,
                              stdout=fsubprocess.PIPE)
        out, _err = p.communicate(input='hello')
        return out

    assert run(body) == b'HELLO'


def test_communicate_stdin_closed_when_no_input():
    # stdin=PIPE but input=None: communicate() must close stdin so the child
    # sees EOF instead of blocking on read().
    def body():
        code = ('import sys; '
                'sys.stdout.write(str(len(getattr(sys.stdin, "buffer", sys.stdin).read())))')
        p = fsubprocess.Popen(_cmd(code),
                              stdin=fsubprocess.PIPE,
                              stdout=fsubprocess.PIPE)
        out, _err = p.communicate()
        return (out, p.returncode)

    assert run(body) == (b'0', 0)


def test_communicate_drains_stderr():
    def body():
        code = ('import sys; sys.stdout.write("out-data"); '
                'sys.stderr.write("err-data")')
        p = fsubprocess.Popen(_cmd(code),
                              stdout=fsubprocess.PIPE,
                              stderr=fsubprocess.PIPE)
        return p.communicate()

    assert run(body) == (b'out-data', b'err-data')


@pytest.mark.parametrize('textkw', ['universal_newlines'] +
                         (['text'] if sys.version_info[0] >= 3 else []))
def test_communicate_text_mode_decodes(textkw):
    def body():
        code = ('import sys; sys.stdout.write("stdout-text"); '
                'sys.stderr.write("stderr-text")')
        kwargs = {'stdout': fsubprocess.PIPE, 'stderr': fsubprocess.PIPE,
                  textkw: True}
        p = fsubprocess.Popen(_cmd(code), **kwargs)
        return p.communicate()

    out, err = run(body)
    assert out == 'stdout-text'
    assert err == 'stderr-text'
    assert isinstance(out, str) and isinstance(err, str)


def test_communicate_feed_survives_broken_pipe():
    # Feed input to a child that already exited without reading stdin: the
    # write hits a closed pipe and the OSError path must swallow it.
    def body():
        p = fsubprocess.Popen(_cmd('pass'), stdin=fsubprocess.PIPE)
        p.wait()  # make sure the read end of the pipe is gone
        out, err = p.communicate(input=b'x' * 1048576)
        return (out, err, p.returncode)

    assert run(body) == (None, None, 0)


def test_communicate_falls_back_without_fil_io(monkeypatch):
    # With no cooperative fd IO available, communicate() defers to the stdlib
    # implementation (which still uses our cooperative wait()).
    monkeypatch.setattr(fsubprocess, '_fil_io', None)

    def body():
        p = fsubprocess.Popen(_cmd('import sys; sys.stdout.write("fb")'),
                              stdout=fsubprocess.PIPE)
        return p.communicate()

    assert run(body) == (b'fb', None)


# --------------------------------------------------------------------------- #
# call / check_call
# --------------------------------------------------------------------------- #

def test_call_returns_exit_code():
    def body():
        ok = fsubprocess.call(_cmd('pass'))
        bad = fsubprocess.call(_cmd('import sys; sys.exit(5)'))
        return (ok, bad)

    assert run(body) == (0, 5)


def test_call_timeout_kills_child_and_raises():
    def body():
        with pytest.raises(fsubprocess.TimeoutExpired):
            fsubprocess.call(_cmd('import time; time.sleep(30)'),
                             timeout=0.1)
        return 'ok'

    assert run(body) == 'ok'


def test_check_call_success():
    def body():
        return fsubprocess.check_call(_cmd('pass'))

    assert run(body) == 0


def test_check_call_raises_calledprocesserror():
    def body():
        cmd = _cmd('import sys; sys.exit(9)')
        with pytest.raises(fsubprocess.CalledProcessError) as excinfo:
            fsubprocess.check_call(cmd)
        return (excinfo.value.returncode, excinfo.value.cmd)

    rc, cmd = run(body)
    assert rc == 9
    assert cmd == _cmd('import sys; sys.exit(9)')


# --------------------------------------------------------------------------- #
# check_output
# --------------------------------------------------------------------------- #

def test_check_output_success():
    def body():
        return fsubprocess.check_output(
            _cmd('import sys; sys.stdout.write("captured")'))

    assert run(body) == b'captured'


def test_check_output_rejects_stdout_kwarg():
    with pytest.raises(ValueError):
        fsubprocess.check_output(_cmd('pass'), stdout=fsubprocess.PIPE)


def test_check_output_with_input_kwarg():
    def body():
        return fsubprocess.check_output(_cmd(_UPPER_CHILD),
                                        stdin=fsubprocess.PIPE,
                                        input=b'abc')

    assert run(body) == b'ABC'


def test_check_output_error_carries_output():
    def body():
        code = 'import sys; sys.stdout.write("partial"); sys.exit(2)'
        with pytest.raises(fsubprocess.CalledProcessError) as excinfo:
            fsubprocess.check_output(_cmd(code))
        return (excinfo.value.returncode, excinfo.value.output)

    assert run(body) == (2, b'partial')


def test_check_output_timeout_kills_child_and_raises():
    def body():
        with pytest.raises(fsubprocess.TimeoutExpired):
            fsubprocess.check_output(_cmd(_CLOSE_OUTPUTS_AND_HANG),
                                     timeout=0.2)
        return 'ok'

    assert run(body) == 'ok'


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #

def test_run_capture_output_success():
    def body():
        code = ('import sys; sys.stdout.write("run-out"); '
                'sys.stderr.write("run-err")')
        cmd = _cmd(code)
        cp = fsubprocess.run(cmd, capture_output=True, check=True)
        return (cp.args, cp.returncode, cp.stdout, cp.stderr)

    args, rc, out, err = run(body)
    assert rc == 0
    assert out == b'run-out'
    assert err == b'run-err'
    assert args[0] == PY


def test_run_check_failure_carries_stderr():
    def body():
        code = 'import sys; sys.stderr.write("boom"); sys.exit(3)'
        with pytest.raises(fsubprocess.CalledProcessError) as excinfo:
            fsubprocess.run(_cmd(code), capture_output=True, check=True)
        e = excinfo.value
        return (e.returncode, e.output, e.stderr)

    assert run(body) == (3, b'', b'boom')


def test_run_no_capture_no_check():
    def body():
        cp = fsubprocess.run(_cmd('import sys; sys.exit(4)'))
        return (cp.returncode, cp.stdout, cp.stderr)

    assert run(body) == (4, None, None)


def test_run_timeout_kills_child_and_raises():
    def body():
        with pytest.raises(fsubprocess.TimeoutExpired):
            fsubprocess.run(_cmd(_CLOSE_OUTPUTS_AND_HANG),
                            capture_output=True, timeout=0.2)
        return 'ok'

    assert run(body) == 'ok'
