import testtools

import filament


class SchedulerTestCase(testtools.TestCase):
    def setUp(self):
        super(SchedulerTestCase, self).setUp()
        self.sched = filament.Scheduler()

        def _nuke_scheduler():
            self.sched.abort()
            del self.sched

        self.addCleanup(_nuke_scheduler)

    def test_sched_abort(self):
        # Covered by setUp()
        pass

    def test_sched_two_filaments(self):
        results = []
        expected_order = ['lt1-1', 'lt2-1', 'lt1-2', 'lt2-2']

        def lt1():
            results.append('lt1-1')
            filament.yield_thread()
            results.append('lt1-2')

        def lt2():
            results.append('lt2-1')
            filament.yield_thread()
            results.append('lt2-2')

        thr1 = filament.spawn(lt1)
        thr2 = filament.spawn(lt2)
        thr1.wait()
        thr2.wait()

        self.assertEqual(expected_order, results)


def test_queue_depth_counts_pending_immediate_wakeups():
    """
    queue_depth() reports (immediate, timers).  The immediate side is the
    per-switch hot path, so sample it while wakeups are actually queued --
    counting an empty list proves nothing.
    """
    import filament

    def body():
        sched = filament.Scheduler()

        # Park several greenthreads on a sleep(0), i.e. an immediate wakeup
        # each, and look at the queue from a greenthread that runs first.
        seen = []

        def yielder():
            filament.sleep(0)

        gts = [filament.spawn(yielder) for _ in range(5)]
        seen.append(sched.queue_depth())
        filament.joinall(gts)
        seen.append(sched.queue_depth())
        return seen

    depths = filament.spawn(body).wait()
    assert depths[0][0] >= 1, depths          # immediates were pending
    assert depths[1] == (0, 0), depths        # and drained afterwards
