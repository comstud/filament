import testtools

import filament


class SchedulerTestCase(testtools.TestCase):
    def setUp(self):
        super(SchedulerTestCase, self).setUp()
        self.sched = filament.scheduler.Scheduler()

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
