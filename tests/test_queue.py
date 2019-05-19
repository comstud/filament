import testtools

from filament import queue
from filament import pyqueue


class _QueueTestMixIn(object):
    def setUp(self):
        super(_QueueTestMixIn, self).setUp()
        self.lifo = False
        try:
            maxsize = self.q_maxsize
            self.queue = self.q_type(maxsize=maxsize)
        except AttributeError:
            self.queue = self.q_type()

    def test_basic_queue_function(self):
        iters = 10
        data = range(iters)
        expected = data[:]
        if self.lifo:
            expected.reverse()
        for x in data:
            self.queue.put(x)
        self.assertEqual(iters, self.queue.qsize())
        results = [self.queue.get() for x in xrange(iters)]
        self.assertEqual(expected, results)
        self.assertTrue(self.queue.empty())
        self.assertFalse(self.queue.full())
        self.assertEqual(0, self.queue.qsize())

    def test_empty_full_size_at_start(self):
        self.assertTrue(self.queue.empty())
        self.assertFalse(self.queue.full())
        self.assertEqual(0, self.queue.qsize())
        self.assertRaises(self.q_module.Empty, self.queue.get, block=False)

    def test_empty_full_after_one_put(self):
        self.queue.put(1)
        self.assertFalse(self.queue.empty())
        self.assertFalse(self.queue.full())
        self.assertEqual(1, self.queue.qsize())


class LiteQueueTestCase(_QueueTestMixIn, testtools.TestCase):
    q_module = pyqueue
    q_type = pyqueue.LiteQueue


class QueueTestCase(_QueueTestMixIn, testtools.TestCase):
    q_module = queue
    q_type = queue.Queue
    q_maxsize = 10

    def test_max_size(self):
        for x in range(10):
            self.queue.put(x)
        self.assertTrue(self.queue.full())
        self.assertRaises(self.q_module.Full, self.queue.put, 10, block=False)


class PyQueueTestCase(QueueTestCase):
    q_module = pyqueue
    q_type = pyqueue.Queue


class LifoQueueTestCase(_QueueTestMixIn, testtools.TestCase):
    q_module = pyqueue
    q_type = pyqueue.LifoQueue

    def setUp(self):
        super(LifoQueueTestCase, self).setUp()
        self.lifo = True
