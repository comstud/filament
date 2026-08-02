# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Reference cycles routed through the C containers must be collectable.

Queue, SimpleQueue and Condition hold strong references to arbitrary user
objects (queued items; the condition's lock).  Without tp_traverse the
collector cannot see those edges, so the ubiquitous "task carries its reply
queue" pattern leaked the queue, the items and everything they pin, forever.
These tests pin each shape with a weakref and demand the cycle actually dies.
"""
import gc
import weakref

from _filament.locking import Condition
from _filament.queue import Queue, SimpleQueue


class Node(object):
    pass


def _assert_dies(build):
    """build() returns one object of the cycle; assert GC reclaims it."""
    obj = build()
    ref = weakref.ref(obj)
    del obj
    gc.collect()
    assert ref() is None, "cycle survived gc.collect(): tp_traverse missing?"


def test_queue_item_cycle_is_collectable():
    def build():
        q = Queue()
        n = Node()
        n.q = q            # item -> queue
        q.put(n)           # queue -> item
        return n
    _assert_dies(build)


def test_queue_many_items_cycle_is_collectable():
    # Items spanning growth of the ring still all traversed.
    def build():
        q = Queue()
        first = Node()
        first.q = q
        q.put(first)
        for _ in range(1000):
            n = Node()
            n.q = q
            q.put(n)
        return first
    _assert_dies(build)


def test_simple_queue_item_cycle_is_collectable():
    def build():
        q = SimpleQueue()
        n = Node()
        n.q = q
        q.put(n)
        return n
    _assert_dies(build)


def test_condition_lock_cycle_is_collectable():
    def build():
        n = Node()
        cond = Condition(lock=n)   # condition -> lock object
        n.cond = cond              # lock object -> condition
        return n
    _assert_dies(build)


def test_queue_self_cycle_is_collectable():
    # Queue itself is not weakref-able; a canary item only dies if the
    # queue's self-cycle does.
    def build():
        q = Queue()
        q.put(q)
        n = Node()
        q.put(n)
        return n
    _assert_dies(build)
