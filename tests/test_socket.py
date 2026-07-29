import sys

import testtools

import filament
from filament import socket
from _filament import socket as _socket
from _filament import queue


_PY3 = sys.version_info[0] >= 3


def _lowlevel(sock):
    # Return the low-level socket that isinstance() checks should target.
    #
    # On Python 3 the stdlib high-level socket *subclasses* the low-level
    # socket (filament's cooperative _filament.socket.socket, or the real
    # _socket.socket), so the high-level object is itself an instance of the
    # low-level type.  On Python 2 the high-level 'socket._socketobject'
    # instead *wraps* the low-level socket as 'self._sock' -- a documented,
    # intentional architectural divergence (see filament/socket.py).  So on
    # Python 2 the isinstance relationship holds on '._sock', not on the
    # wrapper itself.  This keeps the coverage identical on both Pythons while
    # respecting each one's socket object model.
    return sock if _PY3 else sock._sock


class SocketTestCase(testtools.TestCase):
    def test_basic(self):
        self.assertIn('filament', socket.__file__)
        self.assertEqual(1, socket.SOCK_STREAM)
        self.assertEqual(2, socket.AF_INET)
        s = socket.socket()
        # On py3 the stdlib high-level socket subclasses filament's cooperative
        # _filament.socket.socket, so instances are filament sockets. On py2 the
        # relationship is via s._sock (see _lowlevel()).
        self.assertIsInstance(_lowlevel(s), _socket.socket)
        self.assertEqual(socket.AF_INET, s.family)
        self.assertEqual(socket.SOCK_STREAM, s.type)
        self.assertIsInstance(_lowlevel(s), _socket.socket)
        self.assertEqual(0, s.proto)
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, 0)
        self.assertEqual(socket.AF_INET6, s.family)
        self.assertEqual(socket.SOCK_DGRAM, s.type)
        self.assertEqual(0, s.proto)

    def test_filament_socket_doesnt_touch_orig_socket(self):
        # Tests that importing filament.socket doesn't
        # monkey patch 'socket', '_socket', etc. Duplicates
        # a lil bit of test_basic().
        from _filament import socket as _fsocket
        import socket as psocket1
        from filament import socket as fsocket
        import socket as psocket2
        import _socket as _psocket

        self.assertIn('filament', fsocket.__file__)
        self.assertNotIn('filament', psocket1.__file__)

        # filament.socket exposes _realsocket pointing at the cooperative
        # _filament.socket.socket (mirrors the py2 stdlib _realsocket alias).
        self.assertEqual(_fsocket.socket, fsocket._realsocket)
        # The real socket/_socket modules are left untouched, and the filament
        # cooperative socket type is distinct from the real _socket.socket.
        self.assertNotEqual(_fsocket.socket, _psocket.socket)
        self.assertNotEqual(psocket1.gethostbyname, fsocket.gethostbyname)
        self.assertEqual(psocket1.gethostbyname, psocket2.gethostbyname)
        # filament sockets are cooperative _filament.socket.socket instances;
        # the real socket module still yields real _socket.socket instances.
        self.assertIsInstance(_lowlevel(fsocket.socket()), _fsocket.socket)
        self.assertIsInstance(_lowlevel(psocket1.socket()), _psocket.socket)
        self.assertIsInstance(_lowlevel(psocket2.socket()), _psocket.socket)

    def test_dup(self):
        sock = socket.socket()
        nsock = sock.dup()
        self.assertNotEqual(sock, nsock)
        # dup() returns a new, distinct cooperative socket with its own fd.
        self.assertNotEqual(sock.fileno(), nsock.fileno())
        self.assertIsInstance(_lowlevel(nsock), _socket.socket)

    def test_connect_accept(self):
        q = queue.Queue()

        def _listener():
            listener = socket.socket()
            listener.bind(('0.0.0.0', 0))
            listener.listen(128)
            q.put(listener.getsockname())
            ns, peer = listener.accept()
            q.put(peer)
            data = b''
            while 1:
                # just to exercise things, read 1 byte at a time
                d = ns.recv(1)
                if not d:
                    break
                data += d
            q.put(data)

        thr = filament.spawn(_listener)
        s = socket.socket()
        s.connect(q.get())
        self.assertEqual(s.getsockname(), q.get())
        s.send(b'hi there')
        s.close()
        self.assertEqual(b'hi there', q.get())
        thr.wait()

        # test create_connection() also
        thr = filament.spawn(_listener)
        s = socket.create_connection(q.get())
        # On py3 the high-level socket subclasses filament's cooperative socket
        # (_filament.socket.socket); on py2 it wraps it as s._sock (_lowlevel()).
        self.assertIsInstance(_lowlevel(s), _socket.socket)
        self.assertEqual(s.getsockname(), q.get())
        s.send(b'hi there again')
        s.close()
        self.assertEqual(b'hi there again', q.get())
        thr.wait()


def test_kill_beats_ready_data():
    # A greenthread parked in recv() can be woken by data arriving and thrown
    # into (kill / an expiring Timeout) in the same turn.  Two things have to
    # hold when that happens:
    #
    #  * the throw wins over the bytes.  Handing back the data with the
    #    GreenletExit still pending is what CPython reports as "SystemError:
    #    ... returned a result with an exception set" -- it used to fire on
    #    every shutdown of a load test.
    #  * the wakeup that lost the race must not linger.  It used to be queued
    #    with a borrowed pointer to a greenlet that was already finishing, so
    #    the scheduler segfaulted the next time it ran the queue (see the
    #    wakeup contract in include/core/fil_waiter.h).  Hence the loop: one
    #    round never crashed, the second one did.
    for _ in range(20):
        left, right = socket.socketpair()
        outcome = []

        def parked():
            try:
                outcome.append(('data', left.recv(100)))
            except BaseException as e:
                outcome.append(('exc', type(e).__name__))

        greenthread = filament.spawn(parked)
        filament.sleep(0.002)         # let it park inside recv
        right.send(b'hello')          # readable: wakeup queued
        filament.kill(greenthread)    # ... then throw, before it resumes
        # Which of the two wins is a race; the broken third outcome is recv
        # returning its bytes with the exception still set.
        assert outcome in ([('exc', 'GreenletExit')], [('data', b'hello')]), outcome
        left.close()
        right.close()

    # Nothing stale left behind: the scheduler's queues are empty.
    assert filament.Scheduler().queue_depth() == (0, 0)


def test_stale_wakeup_does_not_hit_a_later_wait():
    # The greenthread survives the throw and parks again somewhere else.  A
    # wakeup left over from the first wait used to resume it in the middle of
    # the second one, which surfaced as an untimed Queue.get() reporting a
    # timeout it never had.
    for _ in range(20):
        left, right = socket.socketpair()
        handoff = queue.Queue()
        results = []

        def parked():
            try:
                with filament.Timeout(0.002):
                    left.recv(100)
            except filament.Timeout:
                pass
            results.append(handoff.get())      # must not wake up early

        greenthread = filament.spawn(parked)
        filament.sleep(0.002)
        right.send(b'x')                       # wakeup aimed at the FIRST wait
        filament.sleep(0.002)
        handoff.put('correct')
        greenthread.wait()
        assert results == ['correct'], results
        left.close()
        right.close()


def test_cancelled_wakeup_unlinks_from_the_middle_of_the_run_queue():
    """
    Cancelling a queued wakeup that is not at the head of the immediate FIFO.

    Several greenthreads are signaled in one go, so the run queue holds a
    string of pending switches; killing one that is *not* first makes the
    scheduler unlink an interior node rather than pop the head. Getting that
    unlink wrong corrupts the queue for every other greenthread on it, so the
    assertion is simply that all the survivors still run, in order.
    """
    import filament
    from filament.greenthread import GreenletExit
    from filament.timer import Timer

    def body():
        q = filament.Queue()
        woke = []

        def consumer(i):
            woke.append(("got", i, q.get()))

        gts = [filament.spawn(consumer, i) for i in range(5)]
        filament.sleep(0.01)                 # all five parked on the queue

        # One put per consumer, all before the scheduler runs any of them:
        # five switches now sit in the immediate FIFO.
        for i in range(5):
            q.put(i)

        # Kill an interior one without yielding, so its cancellation has to
        # unlink from the middle of that queue.
        Timer(0, gts[2].throw, GreenletExit)

        filament.sleep(0.2)
        return sorted(w[1] for w in woke)

    survivors = filament.spawn(body).wait()
    # The killed greenthread may or may not have consumed its item first;
    # every other one must have run.
    assert set(survivors) >= {0, 1, 3, 4}, survivors


def test_kill_beats_ready_data_with_a_socket_timeout():
    """
    Same race as test_kill_beats_ready_data, but on a socket that has a
    timeout set.

    A socket with a timeout takes the same cached edge-triggered wait as one
    without, but parks with a deadline armed on the scheduler's timer heap, so
    there is a second way to be resumed. The throw still has to win over bytes
    that arrived in the same wakeup, so both shapes need covering.
    """
    import filament
    from filament import socket as fsocket
    from filament.greenthread import GreenletExit
    from filament.timer import Timer

    def body():
        outcomes = []
        for _ in range(20):
            left, right = fsocket.socketpair()
            left.settimeout(5.0)              # forces the classic path
            parked = []

            def reader():
                try:
                    parked.append(("data", left.recv(16)))
                except GreenletExit:
                    parked.append(("killed", None))
                except Exception as e:        # noqa: B902
                    parked.append(("error", type(e).__name__))

            g = filament.spawn(reader)
            filament.sleep(0.01)              # parked in recv, deadline armed
            Timer(0, g.throw, GreenletExit)   # queue the throw, do not yield
            right.sendall(b"payload")         # ... and make the fd readable
            filament.sleep(0.05)
            outcomes.append(parked[0][0] if parked else "none")
            left.close()
            right.close()
        return outcomes

    outcomes = filament.spawn(body).wait()
    # Either resolution is legitimate; what must not happen is a SystemError
    # from handing back bytes with the exception still pending.
    assert set(outcomes) <= {"data", "killed"}, outcomes


# ---------------------------------------------------------------------------
# Timeouts on the cached edge-triggered io path.
#
# A socket with settimeout() set used to be pushed onto the classic io path,
# which costs two epoll_ctl syscalls, an event_new/event_free and two
# mutex/cond init+destroy pairs for every blocked operation -- real HTTP
# clients set a timeout on every pooled connection, so that was most of the io
# work in a client workload. Such a socket now stays on the cheap cached path
# and the deadline is armed on the scheduler's own timer heap instead of on
# libevent. These cover that the deadline still behaves like a deadline.
# ---------------------------------------------------------------------------


def _elapsed(fn):
    import time

    t0 = time.time()
    result = fn()
    return result, time.time() - t0


def test_recv_timeout_fires_on_the_cached_path():
    """A timeout still fires, and at roughly the requested time."""
    from filament import socket as fsocket

    def body():
        left, right = fsocket.socketpair()
        try:
            left.settimeout(0.2)

            def recv_it():
                try:
                    left.recv(16)
                    return "data"
                except fsocket.timeout:
                    return "timeout"

            return _elapsed(recv_it)
        finally:
            left.close()
            right.close()

    outcome, elapsed = filament.spawn(body).wait()
    assert outcome == "timeout", outcome
    # Lower bound: it must actually wait rather than fire immediately.
    # Upper bound is loose because a loaded box delays the timer.
    assert 0.15 <= elapsed < 3.0, elapsed


def test_recv_timeout_does_not_fire_when_data_arrives_in_time():
    """The armed deadline must not pre-empt a normal wakeup."""
    from filament import socket as fsocket

    def body():
        left, right = fsocket.socketpair()
        try:
            left.settimeout(5.0)
            filament.spawn_later(0.05, right.sendall, b"payload")
            return left.recv(16)
        finally:
            left.close()
            right.close()

    assert filament.spawn(body).wait() == b"payload"


def test_socket_survives_its_own_timeout():
    """
    After a timeout the cached fd-waiter is reused, so a mis-detached waiter
    would show up as the *next* operation hanging or losing its wakeup.
    """
    from filament import socket as fsocket

    def body():
        left, right = fsocket.socketpair()
        try:
            left.settimeout(0.1)
            for _ in range(3):
                try:
                    left.recv(16)
                    return "unexpected data"
                except fsocket.timeout:
                    pass
            # ... and the socket is still perfectly usable afterwards.
            right.sendall(b"still here")
            return left.recv(16)
        finally:
            left.close()
            right.close()

    assert filament.spawn(body).wait() == b"still here"


def test_repeated_timeouts_do_not_accumulate():
    """
    Each call gets its own deadline: three 0.1s timeouts take ~0.3s total, not
    0.1s total (a deadline left armed from the previous call) and not longer.
    """
    from filament import socket as fsocket

    def body():
        left, right = fsocket.socketpair()
        try:
            left.settimeout(0.1)

            def three():
                n = 0
                for _ in range(3):
                    try:
                        left.recv(16)
                    except fsocket.timeout:
                        n += 1
                return n

            return _elapsed(three)
        finally:
            left.close()
            right.close()

    fired, elapsed = filament.spawn(body).wait()
    assert fired == 3, fired
    assert 0.25 <= elapsed < 4.0, elapsed


def test_sendall_timeout_deadline_is_absolute():
    """
    The regression guard for the one genuinely new bug this change could
    introduce: the deadline must be computed ONCE per call, not re-derived
    after each wakeup.

    sendall into a peer that never reads blocks, wakes on a writability edge,
    writes a little more, and blocks again -- many wakeups inside one logical
    operation. With a per-wakeup deadline each edge would push the timeout out
    again and this would never return.
    """
    from filament import socket as fsocket

    def body():
        left, right = fsocket.socketpair()
        try:
            # Small buffers so the send side fills quickly and has to park
            # repeatedly rather than swallowing the payload in one go.
            left.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_SNDBUF, 4096)
            right.setsockopt(fsocket.SOL_SOCKET, fsocket.SO_RCVBUF, 4096)
            left.settimeout(0.3)

            def send_it():
                try:
                    # 'right' is never read, so this cannot complete.
                    left.sendall(b"x" * (8 * 1024 * 1024))
                    return "sent"
                except fsocket.timeout:
                    return "timeout"

            return _elapsed(send_it)
        finally:
            left.close()
            right.close()

    outcome, elapsed = filament.spawn(body).wait()
    assert outcome == "timeout", outcome
    assert 0.25 <= elapsed < 5.0, elapsed


def test_accept_timeout_fires():
    """The same widened guard covers accept() via the FIL_CPROXY_POLL macro."""
    from filament import socket as fsocket

    def body():
        srv = fsocket.socket()
        try:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            srv.settimeout(0.2)

            def accept_it():
                try:
                    srv.accept()
                    return "accepted"
                except fsocket.timeout:
                    return "timeout"

            return _elapsed(accept_it)
        finally:
            srv.close()

    outcome, elapsed = filament.spawn(body).wait()
    assert outcome == "timeout", outcome
    assert 0.15 <= elapsed < 3.0, elapsed
