# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
Group / Pool / GreenPool / GreenPile tests.

The headline properties: a Pool never runs more than ``size`` greenthreads at
once (we measure peak concurrency under load), and GreenPile yields results in
submission order regardless of completion order.
"""

from __future__ import absolute_import

import pytest

import filament


# --------------------------------------------------------------------------- #
# Group
# --------------------------------------------------------------------------- #

def test_group_spawn_and_join():
    g = filament.Group()
    results = []
    for i in range(10):
        g.spawn(lambda i=i: results.append(i))
    g.join()
    assert sorted(results) == list(range(10))


def test_group_len_and_membership():
    g = filament.Group()
    gt = g.spawn(lambda: filament.sleep(0.05))
    assert gt in g
    assert len(g) >= 1
    g.join()


def test_group_map_ordered():
    g = filament.Group()
    assert g.map(lambda x: x * x, [1, 2, 3, 4, 5]) == [1, 4, 9, 16, 25]


def test_group_imap_ordered():
    g = filament.Group()
    assert list(g.imap(lambda x: x + 1, [10, 20, 30])) == [11, 21, 31]


def test_group_imap_reraises():
    g = filament.Group()

    def f(x):
        if x == 2:
            raise ValueError("two")
        return x

    with pytest.raises(ValueError):
        list(g.imap(f, [1, 2, 3]))


def test_group_kill():
    g = filament.Group()

    def loop():
        while True:
            filament.sleep(0.001)

    gts = [g.spawn(loop) for _ in range(5)]
    filament.sleep(0)
    g.kill()
    assert all(gt.dead for gt in gts)


# --------------------------------------------------------------------------- #
# Pool concurrency cap
# --------------------------------------------------------------------------- #

def _measure_peak(pool, n_tasks, hold=0.01):
    cur = [0]
    peak = [0]

    def work(i):
        cur[0] += 1
        if cur[0] > peak[0]:
            peak[0] = cur[0]
        filament.sleep(hold)
        cur[0] -= 1

    gts = [pool.spawn(work, i) for i in range(n_tasks)]
    pool.join()
    return peak[0]


def test_pool_enforces_cap():
    pool = filament.Pool(5)
    peak = _measure_peak(pool, 40)
    assert peak <= 5
    # Under load with 40 tasks and a cap of 5, we should actually reach the cap.
    assert peak == 5


def test_pool_size_one_serializes():
    pool = filament.Pool(1)
    peak = _measure_peak(pool, 10)
    assert peak == 1


def test_pool_running_and_free_counts():
    pool = filament.Pool(3)

    def work():
        filament.sleep(0.05)

    gts = [pool.spawn(work) for _ in range(3)]
    filament.sleep(0)  # let all 3 enter
    assert pool.running() == 3
    assert pool.free_count() == 0
    pool.join()
    assert pool.running() == 0
    assert pool.free_count() == 3


def test_pool_waiting_count():
    pool = filament.Pool(1)

    def work():
        filament.sleep(0.05)

    first = pool.spawn(work)
    # These block in spawn because the single slot is taken.
    others = []

    def spawner():
        others.append(pool.spawn(work))
        others.append(pool.spawn(work))

    s = filament.spawn(spawner)
    filament.sleep(0)
    # At least one greenthread is now waiting for a slot.
    assert pool.waiting() >= 1
    pool.join()
    s.wait()


def test_pool_unbounded():
    pool = filament.Pool()  # size=None -> unbounded
    peak = _measure_peak(pool, 30)
    assert peak == 30


def test_greenpool_default_size():
    gp = filament.GreenPool()
    assert gp.size == 1000


def test_greenpool_cap():
    gp = filament.GreenPool(4)
    assert _measure_peak(gp, 20) <= 4


def test_pool_resize_grow():
    pool = filament.Pool(2)
    pool.resize(5)
    assert pool.size == 5
    assert _measure_peak(pool, 20) <= 5


# --------------------------------------------------------------------------- #
# GreenPile ordered results
# --------------------------------------------------------------------------- #

def test_greenpile_ordered_results():
    pile = filament.GreenPile(4)

    def work(i):
        # Reverse-ish delays so later items would finish first.
        filament.sleep(0.005 * ((10 - i) % 6))
        return i * 2

    for i in range(10):
        pile.spawn(work, i)
    assert list(pile) == [i * 2 for i in range(10)]


def test_greenpile_reraises_in_order():
    pile = filament.GreenPile(4)

    def work(i):
        if i == 1:
            raise ValueError("one")
        return i

    pile.spawn(work, 0)
    pile.spawn(work, 1)
    pile.spawn(work, 2)
    it = iter(pile)
    assert next(it) == 0
    with pytest.raises(ValueError):
        next(it)


def test_greenpile_from_existing_pool():
    pool = filament.GreenPool(3)
    pile = filament.GreenPile(pool)
    for i in range(6):
        pile.spawn(lambda i=i: i + 100)
    assert list(pile) == [100, 101, 102, 103, 104, 105]


def test_pool_starmap():
    pool = filament.Pool(4)
    assert pool.starmap(lambda a, b: a + b, [(1, 2), (3, 4), (5, 6)]) == [3, 7, 11]


def test_pool_imap_unordered_all_present():
    pool = filament.Pool(4)
    results = set(pool.imap_unordered(lambda x: x * x, [1, 2, 3, 4, 5]))
    assert results == set([1, 4, 9, 16, 25])
