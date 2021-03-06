import collections
import heapq

try:
    import Queue as _queue
except ImportError:
    import queue as _queue

import filament
from filament import locking

Empty = _queue.Empty
Full = _queue.Full

class LiteQueue(object):
    __slots__ = ('getters', 'lock', 'not_empty_cond', 'queue')

    def __init__(self):
        self.getters = []
        self.lock = locking.Lock()
        self.not_empty_cond = locking.Condition(lock=self.lock)
        self._init()

    def _init(self):
        self.queue = collections.deque()

    def _get(self):
        return self.queue.popleft()

    _get_guts = _get

    def qsize(self):
        with self.lock:
            return len(self.queue)

    def empty(self):
        with self.lock:
            return len(self.queue) == 0

    def full(self):
        return False

    def get(self, block=True, timeout=None):
        with self.lock:
            try:
                return self._get_guts()
            except IndexError:
                if not block:
                    raise Empty()
            self.not_empty_cond.wait(timeout=timeout)
            return self._get()

    def _put(self, item):
        self.queue.append(item)

    def _put_guts(self, item, block, timeout):
        self._put(item)
        if len(self.queue) == 1:
            self.not_empty_cond.notify()

    def put(self, item):
        with self.lock:
            return self._put_guts(item, True, None)

    def put_nowait(self, item):
        return self.put(item)

    def get_nowait(self, item):
        return self.get(block=False)


class Queue(LiteQueue):
    __slots__ = ('not_full_cond', 'tasks_done_cond', 'maxsize', 'unfinished_tasks')

    def __init__(self, maxsize=0):
        super(Queue, self).__init__()
        self.not_full_cond = locking.Condition(lock=self.lock)
        self.tasks_done_cond = locking.Condition(lock=self.lock)
        self.maxsize = maxsize
        self.unfinished_tasks = 0

    def full(self):
        if self.maxsize <= 0:
            return False
        with self.lock:
            return len(self.queue) == self.maxsize

    def _get_guts(self):
        item = self._get()
        if self.maxsize > 0 and len(self.queue) == self.maxsize - 1:
            # We made room!
            self.not_full_cond.notify()
        return item

    def _put_guts(self, item, block, timeout):
        if self.maxsize > 0 and len(self.queue) >= self.maxsize:
            if not block:
                raise Full
            self.not_full_cond.wait(timeout=timeout)
        super(Queue, self)._put_guts(item, block, timeout)
        self.unfinished_tasks += 1

    def put(self, item, block=True, timeout=None):
        with self.lock:
            return self._put_guts(item, block, timeout)
        filament.yield_thread()

    def put_nowait(self, item):
        return self.put(item, block=False)

    def task_done(self):
        with self.lock:
            if self.unfinished_tasks <= 1:
                if self.unfinished_tasks == 0:
                    raise ValueError('task_done() called too many times')
                self.tasks_done_cond.notify_all()
            self.unfinished_tasks -= 1

    def join(self):
        with self.lock:
            while self.unfinished_tasks:
                self.tasks_done_cond.wait()


class PriorityQueue(Queue):
    __slots__ = ('queue')

    def _init(self):
        self.queue = []

    def _put(self, item):
        heapq.heappush(self.queue, item)

    def _get(self):
        return heapq.heappop(self.queue)


class LifoQueue(Queue):
    __slots__ = ('queue')

    def _init(self):
        self.queue = []

    def _get(self):
        return self.queue.pop()
