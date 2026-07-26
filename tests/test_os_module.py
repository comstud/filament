# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Tests for ``filament.os`` (used directly, WITHOUT monkey-patching):

  * ``read``/``write`` are the cooperative C helpers and cooperate on a pipe
    (a read parked in one filament completes when another filament writes).
  * ``NBFile``: attribute delegation, ``fileno()`` returning a filament
    ``FDesc`` carrying ``_fil_sock``, and the cooperative read/write retry
    loops (EAGAIN -> wait-for-ready -> retry; anything else re-raised).
  * ``fdopen``: all mode-detection branches (regular file, fifo, fd already
    non-blocking, FDesc-with-``_fil_sock`` inherit path, and the defensive
    fstat/F_SETFL failure fallbacks).
"""

from __future__ import absolute_import

import errno
import fcntl
import os as _real_os
import sys

import pytest

import filament
from filament import io as fil_io
from filament import os as fos


def run(fn):
    return filament.spawn(fn).wait()


def _set_nonblocking(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | _real_os.O_NONBLOCK)


class _RawEnd(object):
    """Minimal file-like wrapper over a raw fd.

    Unlike py3 buffered/raw io objects (which return ``None`` on EAGAIN),
    ``os.read``/``os.write`` raise ``OSError(EAGAIN)`` when a non-blocking fd
    would block -- exactly the behavior ``NBFile``'s retry loop handles.
    """

    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd

    def read(self, size=-1):
        if size is None or size < 0:
            size = 65536
        return _real_os.read(self._fd, size)

    def write(self, data):
        return _real_os.write(self._fd, data)

    def marker(self):
        return "delegated"


class _ErrRaiser(object):
    """File-like whose read/write always raise a given errno."""

    def __init__(self, err):
        self._err = err

    def fileno(self):
        return -1

    def read(self, size=-1):
        raise OSError(self._err, _real_os.strerror(self._err))

    def write(self, data):
        raise OSError(self._err, _real_os.strerror(self._err))


# --------------------------------------------------------------------------- #
# module-level read/write aliases
# --------------------------------------------------------------------------- #

def test_read_write_are_cooperative_aliases():
    assert fos.read is fil_io.os_read
    assert fos.write is fil_io.os_write
    # The patcher marker requests item-level patching of exactly these names.
    assert fos.__filament__["patch"] == "os"
    assert sorted(fos.__filament__["items"]) == ["fdopen", "read", "write"]


def test_os_read_parks_until_peer_filament_writes():
    def body():
        r, w = _real_os.pipe()
        got = []

        def reader():
            got.append(fos.read(r, 10))

        g = filament.spawn(reader)
        filament.sleep(0.01)        # reader is parked waiting for data
        assert got == []
        fos.write(w, b"ping")
        g.wait()
        _real_os.close(r)
        _real_os.close(w)
        return got

    assert run(body) == [b"ping"]


# --------------------------------------------------------------------------- #
# NBFile basics: init, delegation, fileno/_raw_fileno
# --------------------------------------------------------------------------- #

def test_nbfile_init_delegation_and_fileno():
    r, w = _real_os.pipe()
    try:
        nb = fos.NBFile(_RawEnd(r))
        assert nb._act_nonblocking is True
        assert errno.EAGAIN in nb._blocking_errnos
        # Anything NBFile does not override is delegated to the wrapped file.
        assert nb.marker() == "delegated"
        # fileno() hands back a filament FDesc that remembers its NBFile.
        fd = nb.fileno()
        assert isinstance(fd, fil_io.FDesc)
        assert int(fd) == r
        assert fd._fil_sock is nb
        assert nb._raw_fileno() == r
    finally:
        _real_os.close(r)
        _real_os.close(w)


# --------------------------------------------------------------------------- #
# NBFile cooperative read
# --------------------------------------------------------------------------- #

def test_nbfile_sized_read_waits_for_data():
    def body():
        r, w = _real_os.pipe()
        _set_nonblocking(r)
        nb = fos.NBFile(_RawEnd(r))
        got = []

        def reader():
            got.append(nb.read(5))

        g = filament.spawn(reader)
        filament.sleep(0.01)        # reader hit EAGAIN and parked
        assert got == []
        _real_os.write(w, b"hello")
        g.wait()
        _real_os.close(r)
        _real_os.close(w)
        return got

    assert run(body) == [b"hello"]


def test_nbfile_unsized_read_variants():
    r, w = _real_os.pipe()
    _set_nonblocking(r)
    nb = fos.NBFile(_RawEnd(r))
    # size=None takes the unsized branch.
    _real_os.write(w, b"ab")
    assert nb.read(None) == b"ab"
    # Default size=-1 takes the unsized branch too.
    _real_os.write(w, b"cd")
    assert nb.read() == b"cd"
    # EOF: writer closed, unsized read returns empty without blocking.
    _real_os.close(w)
    assert nb.read() == b""
    _real_os.close(r)


def test_nbfile_write_waits_until_pipe_drained():
    def body():
        r, w = _real_os.pipe()
        _set_nonblocking(r)
        _set_nonblocking(w)
        nb = fos.NBFile(_RawEnd(w))

        # Fill the pipe buffer until a plain write would block.
        for _ in range(64):
            try:
                _real_os.write(w, b"x" * 4096)
            except OSError as e:
                assert e.errno in nb._blocking_errnos
                break
        else:
            pytest.fail("pipe buffer never filled")

        done = []

        def writer():
            done.append(nb.write(b"tail"))

        g = filament.spawn(writer)
        filament.sleep(0.01)        # writer hit EAGAIN and parked
        assert done == []

        # Drain the pipe from this filament; the parked writer then retries.
        while True:
            try:
                chunk = _real_os.read(r, 65536)
            except OSError:
                break
            if not chunk:
                break
        g.wait()
        assert done == [4]          # os.write byte count for b"tail"
        tail = _real_os.read(r, 65536)
        _real_os.close(r)
        _real_os.close(w)
        return tail

    assert run(body) == b"tail"


def test_nbfile_non_eagain_errors_reraised():
    nb = fos.NBFile(_ErrRaiser(errno.EPIPE))
    with pytest.raises(OSError) as ei:
        nb.read(1)
    assert ei.value.errno == errno.EPIPE
    with pytest.raises(OSError) as ei:
        nb.write(b"x")
    assert ei.value.errno == errno.EPIPE


def test_nbfile_blocking_mode_reraises_eagain():
    # When the fd is considered blocking, EAGAIN is not swallowed: it means
    # something unexpected, so it propagates.
    nb = fos.NBFile(_ErrRaiser(errno.EAGAIN))
    nb._act_nonblocking = False
    with pytest.raises(OSError) as ei:
        nb.read(1)
    assert ei.value.errno == errno.EAGAIN
    with pytest.raises(OSError) as ei:
        nb.write(b"x")
    assert ei.value.errno == errno.EAGAIN


# --------------------------------------------------------------------------- #
# fdopen branches
# --------------------------------------------------------------------------- #

def test_fdopen_regular_file_current_behavior(tmp_path):
    # NOTE ON A LIBRARY BUG (documented, not worked around): fdopen intends to
    # flip only fifos/sockets/char devices to non-blocking, but its test is
    #   stat.S_IFMT(st.st_mode) & (S_IFIFO | S_IFSOCK | S_IFCHR)
    # and that mask ORs to 0o170000 -- the entire file-format field -- so
    # EVERY file type (including S_IFREG) matches and takes the fifo branch.
    # We pin the current behavior: a regular file also gets O_NONBLOCK set and
    # _act_nonblocking False (harmless in practice: regular files never
    # return EAGAIN).
    path = str(tmp_path / "plain.txt")
    fd = _real_os.open(path, _real_os.O_RDWR | _real_os.O_CREAT, 0o600)
    f = fos.fdopen(fd, "w+")
    assert isinstance(f, fos.NBFile)
    assert f._act_nonblocking is False
    flags = fcntl.fcntl(f._raw_fileno(), fcntl.F_GETFL)
    assert flags & _real_os.O_NONBLOCK
    # py2's file.write returns None; py3's returns the byte count.
    assert f.write("hello") in (5, None)
    f.seek(0)                       # delegated via __getattr__
    assert f.read() == "hello"
    f.close()


def test_fdopen_pipe_fd_flipped_to_nonblocking():
    r, w = _real_os.pipe()
    f = fos.fdopen(r, "rb")
    assert isinstance(f, fos.NBFile)
    assert f._act_nonblocking is False
    flags = fcntl.fcntl(r, fcntl.F_GETFL)
    assert flags & _real_os.O_NONBLOCK
    f.close()
    _real_os.close(w)


def test_fdopen_fd_already_nonblocking():
    r, w = _real_os.pipe()
    _set_nonblocking(r)
    f = fos.fdopen(r, "rb")
    assert f._act_nonblocking is True
    f.close()
    _real_os.close(w)


def test_fdopen_inherits_mode_from_fdesc_fil_sock():
    r, w = _real_os.pipe()
    f1 = fos.fdopen(r, "rb")        # fifo branch: fd now O_NONBLOCK,
    assert f1._act_nonblocking is False
    fdesc = f1.fileno()
    assert fdesc._fil_sock is f1
    # Passing the FDesc back into fdopen must inherit f1's mode.  The fd IS
    # O_NONBLOCK at this point, so if the inherit path were skipped the
    # O_NONBLOCK check would give True instead -- False proves inheritance.
    # (py2's fdopen has no closefd; hand it a dup'd fd so the second file
    # object owns its own descriptor on both versions.)
    if sys.version_info[0] >= 3:
        f2 = fos.fdopen(fdesc, "rb", closefd=False)
    else:
        dup_fd = _real_os.dup(int(fdesc))
        dup_desc = fil_io.FDesc(dup_fd)
        dup_desc._fil_sock = fdesc._fil_sock
        f2 = fos.fdopen(dup_desc, "rb")
    assert f2._act_nonblocking is False
    if sys.version_info[0] < 3:
        f2.close()
    f1.close()
    _real_os.close(w)


def test_fdopen_fstat_failure_falls_back_to_blocking(monkeypatch):
    class _FakeOS(object):
        O_NONBLOCK = _real_os.O_NONBLOCK

        @staticmethod
        def fstat(fd):
            raise OSError(errno.EBADF, "forced fstat failure")

    monkeypatch.setattr(fos, "_orig_os", _FakeOS)
    r, w = _real_os.pipe()
    f = fos.fdopen(r, "rb")
    assert f._act_nonblocking is True
    f.close()
    _real_os.close(w)


def test_fdopen_unknown_file_type_stays_blocking(monkeypatch):
    # The "not a fifo/socket/chardev" else-branch is unreachable with a real
    # fstat (see the mask-overlap note above), so feed fdopen a stat result
    # whose format bits are zero.
    class _St(object):
        st_mode = 0

    class _FakeOS(object):
        O_NONBLOCK = _real_os.O_NONBLOCK

        @staticmethod
        def fstat(fd):
            return _St()

    monkeypatch.setattr(fos, "_orig_os", _FakeOS)
    r, w = _real_os.pipe()
    f = fos.fdopen(r, "rb")
    assert f._act_nonblocking is True
    f.close()
    _real_os.close(w)


def test_fdopen_setfl_failure_falls_back_to_blocking(monkeypatch):
    real_fcntl = fcntl.fcntl

    class _FakeFcntl(object):
        F_GETFL = fcntl.F_GETFL
        F_SETFL = fcntl.F_SETFL

        @staticmethod
        def fcntl(fd, op, *args):
            if op == fcntl.F_SETFL:
                raise IOError(errno.EINVAL, "forced F_SETFL failure")
            return real_fcntl(fd, op, *args)

    monkeypatch.setattr(fos, "fcntl", _FakeFcntl)
    r, w = _real_os.pipe()
    f = fos.fdopen(r, "rb")         # fifo branch, but flipping fails
    assert f._act_nonblocking is True
    f.close()
    _real_os.close(w)


def test_os_write_and_read_work_on_a_regular_file(tmp_path):
    """
    Regular files must take a direct syscall, not the io thread.

    epoll refuses regular files outright -- they are by definition always
    ready, so there is nothing to poll for -- and routing them to the io
    thread made ``event_add()`` fail with ``RuntimeError: Couldn't add
    event``.  That broke any ``os.write()`` to a file under monkey-patching,
    which is what ``tempfile`` does, so a WSGI app whose framework spilled a
    large request body to disk died with a 500.
    """
    path = str(tmp_path / "regular.bin")
    payload = b"x" * (256 * 1024)

    def body():
        fd = _real_os.open(path, _real_os.O_RDWR | _real_os.O_CREAT, 0o600)
        try:
            written = 0
            while written < len(payload):
                written += fos.write(fd, payload[written:])
            assert written == len(payload)
            _real_os.lseek(fd, 0, _real_os.SEEK_SET)
            got = b""
            while len(got) < len(payload):
                chunk = fos.read(fd, 65536)
                if not chunk:
                    break
                got += chunk
            return got
        finally:
            _real_os.close(fd)

    assert run(body) == payload


def test_os_write_to_a_directory_fd_reports_oserror(tmp_path):
    # A directory is also unpollable; it must reach the real syscall and
    # surface its normal EBADF/EISDIR rather than an io-thread RuntimeError.
    fd = _real_os.open(str(tmp_path), _real_os.O_RDONLY)
    try:
        with pytest.raises(OSError):
            run(lambda: fos.write(fd, b"nope"))
    finally:
        _real_os.close(fd)


def test_monkey_patched_tempfile_roundtrips_a_large_body():
    # The end-to-end shape of the original failure: patch everything, then let
    # tempfile create and write a spill file the way a WSGI framework would.
    from tests._helpers import run_py

    res = run_py('''
import filament.patcher
filament.patcher.patch_all()
import tempfile

payload = b"x" * (512 * 1024)
with tempfile.NamedTemporaryFile() as fh:
    fh.write(payload)
    fh.flush()
    fh.seek(0)
    assert fh.read() == payload
print("OK")
''')
    assert not res.timed_out, repr(res)
    assert res.returncode == 0, repr(res)
    assert "OK" in res.stdout, repr(res)
