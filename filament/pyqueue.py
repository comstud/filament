"""Pure-Python cooperative queues (fallback for ``_filament.queue``).

These implement the ``queue.Queue`` family on top of filament's cooperative
``Lock``/``Condition`` primitives, so that blocking ``get``/``put`` calls yield
to the scheduler instead of blocking the OS thread.  The C ``_filament.queue``
module is preferred in production (see ``filament/queue.py``); this module is
the readable, dependency-light fallback and is handy for platforms where the C
queue is unavailable.

The overridable-hook structure (``_init``/``_put``/``_get``) and the
``task_done``/``join`` logic are derived from CPython's ``Lib/queue.py``
(Python Software Foundation License Version 2; see ``LICENSE.PSF``).

All of the historic bugs are fixed here:

* ``LiteQueue.get`` now re-checks emptiness in a loop after waking (spurious
  wakeups / multiple waiters) and turns a wait-timeout into ``Empty``.
* ``get_nowait`` takes no spurious ``item`` argument.
* ``_put_guts`` honours ``block``/``timeout`` on bounded queues.
* The unreachable ``yield_thread()`` after ``return`` in ``put`` is gone.
"""

# NB: required on py2 -- implicit relative imports would otherwise
# resolve stdlib names (os/time) to filament's own sibling modules.
from __future__ import absolute_import

import collections
import heapq
import time as _time

# ``Empty``/``Full`` live in ``queue`` (Py3) or ``Queue`` (Py2).
try:
    import queue as _queue
except ImportError:  # pragma: no cover - Python 2
    import Queue as _queue

from filament import locking
from filament import exc as _fil_exc

Empty = _queue.Empty
Full = _queue.Full


class LiteQueue(object):
    """An unbounded FIFO queue -- the minimal cooperative queue."""

    __slots__ = ('lock', 'not_empty_cond', 'queue')

    def __init__(self):
        self.lock = locking.Lock()
        self.not_empty_cond = locking.Condition(lock=self.lock)
        self._init()

    # -- hooks subclasses override to change the storage discipline --------
    def _init(self):
        self.queue = collections.deque()

    def _put(self, item):
        self.queue.append(item)

    def _get(self):
        return self.queue.popleft()

    # -- introspection -----------------------------------------------------
    def qsize(self):
        with self.lock:
            return len(self.queue)

    def empty(self):
        with self.lock:
            return len(self.queue) == 0

    def full(self):
        return False

    # -- core operations ---------------------------------------------------
    def _get_guts(self):
        # Overridden by bounded Queue to also signal not-full.
        return self._get()

    def get(self, block=True, timeout=None):
        with self.lock:
            # Loop rather than a single ``if``: another greenthread may have
            # taken the item between the notify and our reacquiring the lock.
            while len(self.queue) == 0:
                if not block:
                    raise Empty()
                try:
                    self.not_empty_cond.wait(timeout=timeout)
                except _fil_exc.Timeout as e:
                    if type(e) is not _fil_exc.Timeout:
                        raise      # an outer with-Timeout fired; propagate
                    # Cooperative wait timed out -> nothing to get.
                    raise Empty()
            return self._get_guts()

    def get_nowait(self):
        return self.get(block=False)

    # -- iteration (gevent parity) -----------------------------------------
    # ``for item in q`` blocks on get() and ends when the ``StopIteration``
    # class itself is pulled from the queue.
    def __iter__(self):
        return self

    def __next__(self):
        result = self.get()
        if result is StopIteration:
            raise result
        return result

    next = __next__            # Py2

    def __len__(self):
        return self.qsize()

    def __bool__(self):
        # gevent queues are unconditionally truthy; with __len__ defined an
        # empty queue would otherwise be falsy.
        return True

    __nonzero__ = __bool__     # Py2

    def _put_guts(self, item, block, timeout):
        # LiteQueue is unbounded, so put never blocks; just store and wake one
        # waiting getter.
        self._put(item)
        self.not_empty_cond.notify()

    def put(self, item, block=True, timeout=None):
        with self.lock:
            self._put_guts(item, block, timeout)

    def put_nowait(self, item):
        return self.put(item, block=False)


class Queue(LiteQueue):
    """A bounded FIFO queue with ``task_done``/``join`` support."""

    __slots__ = ('not_full_cond', 'tasks_done_cond', 'maxsize', '_bound',
                 'unfinished_tasks')

    def __init__(self, maxsize=None):
        super(Queue, self).__init__()
        self.not_full_cond = locking.Condition(lock=self.lock)
        self.tasks_done_cond = locking.Condition(lock=self.lock)
        # gevent semantics: None, 0, and negatives all mean unbounded, and the
        # public ``maxsize`` attribute reads None when unbounded.
        if maxsize is None or maxsize <= 0:
            self.maxsize = None
            self._bound = 0
        else:
            self.maxsize = maxsize
            self._bound = maxsize
        self.unfinished_tasks = 0

    def full(self):
        if self._bound <= 0:
            return False
        with self.lock:
            return len(self.queue) >= self._bound

    def _get_guts(self):
        item = self._get()
        # We just freed a slot; wake a blocked putter.
        if self._bound > 0:
            self.not_full_cond.notify()
        return item

    def _put_guts(self, item, block, timeout):
        if self._bound > 0:
            # Wait (cooperatively) for room, honouring block/timeout.
            while len(self.queue) >= self._bound:
                if not block:
                    raise Full()
                try:
                    self.not_full_cond.wait(timeout=timeout)
                except _fil_exc.Timeout as e:
                    if type(e) is not _fil_exc.Timeout:
                        raise      # an outer with-Timeout fired; propagate
                    raise Full()
        self._put(item)
        self.unfinished_tasks += 1
        self.not_empty_cond.notify()

    def task_done(self):
        with self.lock:
            unfinished = self.unfinished_tasks - 1
            if unfinished < 0:
                raise ValueError('task_done() called too many times')
            if unfinished == 0:
                self.tasks_done_cond.notify_all()
            self.unfinished_tasks = unfinished

    def join(self, timeout=None):
        """
        Block until all items have been processed (or ``timeout`` elapses).

        Returns True when the task count hit zero, False on timeout (gevent
        parity).
        """
        deadline = None if timeout is None else _time.time() + timeout
        with self.lock:
            while self.unfinished_tasks:
                if deadline is None:
                    remaining = None
                else:
                    remaining = deadline - _time.time()
                    if remaining <= 0:
                        return False
                try:
                    self.tasks_done_cond.wait(remaining)
                except _fil_exc.Timeout as e:
                    if type(e) is not _fil_exc.Timeout:
                        raise      # an outer with-Timeout fired; propagate
                    return False
            return True


class PriorityQueue(Queue):
    """A bounded priority queue (lowest value first)."""

    __slots__ = ()

    def _init(self):
        self.queue = []

    def _put(self, item):
        heapq.heappush(self.queue, item)

    def _get(self):
        return heapq.heappop(self.queue)


class LifoQueue(Queue):
    """A bounded LIFO (stack) queue."""

    __slots__ = ()

    def _init(self):
        self.queue = []

    def _put(self, item):
        self.queue.append(item)

    def _get(self):
        return self.queue.pop()
