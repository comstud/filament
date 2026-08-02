/*
 * The MIT License (MIT): http://opensource.org/licenses/mit-license.php
 *
 * Copyright (c) 2019, Chris Behrens
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 *
 */
#ifndef __FIL_CORE_WFIFOQ_H__
#define __FIL_CORE_WFIFOQ_H__

#include "core/filament.h"

/*
 * Mutual exclusion for the queue's state.
 *
 * On a normal build there is none, and none is needed: every operation below
 * runs with the GIL held, and the only place it is released is inside the wait
 * -- where the loop re-tests the condition afterwards.  That is the whole
 * design and it stays exactly as it was; FIL_WFIFOQ_LOCK compiles to nothing
 * and the struct does not grow.
 *
 * On a FREE-THREADING build (PEP 703) the GIL is not there to do it, and this
 * queue is explicitly documented as usable from native OS threads and
 * greenthreads at the same time -- so two real threads can be inside put() and
 * get() concurrently.  Unprotected, that races on three separate things: the
 * ring buffer itself (fil_fifoq_put/get), the intrusive waiter lists (an add is
 * four pointer stores, a delete is two), and the emptiness/fullness test that
 * decides whether to wait at all.  Symptoms seen: a native thread hung forever
 * on a queue that had items (its wakeup went to a corrupted list), and a
 * blocking get() raising Empty because two getters both passed the len test and
 * only one item existed.
 *
 * The lock is held across the state test AND the decision to wait, and dropped
 * only inside fil_waiterlist_wait_locked() for the wait itself.  Lock order is
 * qlock -> waiter_lock -> sched_lock, matching the io layer's, and nothing
 * takes them in reverse.
 */
#ifdef Py_GIL_DISABLED
#  define FIL_WFIFOQ_LOCK(__q)    pthread_mutex_lock(&((__q)->lock))
#  define FIL_WFIFOQ_UNLOCK(__q)  pthread_mutex_unlock(&((__q)->lock))
#  define FIL_WFIFOQ_LOCKP(__q)   (&((__q)->lock))
#  define FIL_WFIFOQ_WAIT(__q, __list, __ts, __exc) \
       fil_waiterlist_wait_locked(__list, __ts, __exc, FIL_WFIFOQ_LOCKP(__q))
#else
#  define FIL_WFIFOQ_LOCK(__q)    ((void)0)
#  define FIL_WFIFOQ_UNLOCK(__q)  ((void)0)
#  define FIL_WFIFOQ_LOCKP(__q)   NULL
#  define FIL_WFIFOQ_WAIT(__q, __list, __ts, __exc) \
       fil_waiterlist_wait(__list, __ts, __exc)
#endif

typedef struct _fil_wfifoq {
    int _queue_inited;
    uint64_t max_size;
    FilFifoQ queue;
    PyObject *empty_error;
    PyObject *full_error;

    FilWaiterList getters;
    FilWaiterList putters;
#ifdef Py_GIL_DISABLED
    pthread_mutex_t lock;
#endif
} FilWFifoQ;

#define fil_wfifoq_len(__q) ((__q)->queue.len)
#define fil_wfifoq_empty(__q) (fil_wfifoq_len(__q) == 0)
/*
 * q->queue.len and q->max_size are both uint64_t and max_size
 * is set to (uint64_t)-1 if unlimited, so this simple check works fine:
 */
#define fil_wfifoq_full(__q) ((__q)->queue.len >= (__q)->max_size)

static inline int fil_wfifoq_init(FilWFifoQ *q, uint64_t max_size, PyObject *empty_error, PyObject *full_error)
{
    if (fil_fifoq_init(&(q->queue)))
    {
        PyErr_SetString(PyExc_MemoryError, "out of memory allocating queue chunk");
        return -1;
    }
    q->_queue_inited = 1;
    if (max_size == 0)
    {
        max_size = (uint64_t)-1;
    }
    q->max_size = max_size;
    Py_INCREF(empty_error);
    q->empty_error = empty_error;
    Py_INCREF(full_error);
    q->full_error = full_error;
    fil_waiterlist_init(q->getters);
    fil_waiterlist_init(q->putters);
#ifdef Py_GIL_DISABLED
    pthread_mutex_init(&(q->lock), NULL);
#endif
    return 0;
}

/*
 * GC support for the owning Python object.
 *
 * Traverse visits every queued item in place (each holds the reference
 * _fil_wfifoq_put took for the getter that never came) plus the two error
 * classes.  No locking even on free-threading builds: the collector runs the
 * world stopped, and on stock builds the GIL is held.
 */
static inline int fil_wfifoq_traverse(FilWFifoQ *q, visitproc visit, void *arg)
{
    if (q->_queue_inited && q->queue.len > 0)
    {
        FilFifoQChunk *chunk = q->queue.head;
        uint64_t idx = q->queue.pop_idx;

        for (;;)
        {
            /* append_idx is the last WRITTEN slot of a chunk; interior
             * chunks are full through FIL_FIFOQ_CHUNK_SIZE - 1. */
            uint64_t last = (chunk == q->queue.tail)
                ? chunk->append_idx
                : (uint64_t)(FIL_FIFOQ_CHUNK_SIZE - 1);

            for (; idx <= last; idx++)
            {
                Py_VISIT((PyObject *)chunk->items[idx]);
            }
            if (chunk == q->queue.tail)
            {
                break;
            }
            chunk = chunk->next_chunk;
            idx = 0;
        }
    }
    Py_VISIT(q->empty_error);
    Py_VISIT(q->full_error);
    return 0;
}

/* Drop every queued item (tp_clear, and the head of deinit).  The queue
 * stays valid and usable afterwards -- just empty. */
static inline void fil_wfifoq_clear_items(FilWFifoQ *q)
{
    void *item;

    if (q->_queue_inited)
    {
        while (fil_fifoq_get(&(q->queue), &item) == 0)
        {
            Py_DECREF((PyObject *)item);
        }
    }
}

static inline void fil_wfifoq_deinit(FilWFifoQ *q)
{
    assert(fil_waiterlist_empty(q->getters));
    assert(fil_waiterlist_empty(q->putters));
    if (q->_queue_inited)
    {
        /* Every item in the ring holds the reference _fil_wfifoq_put took
         * for the getter that never came; dropping the chunks without
         * dropping those references leaks every item still queued at
         * dealloc. */
        fil_wfifoq_clear_items(q);
        fil_fifoq_deinit(&(q->queue));
    }
    Py_CLEAR(q->empty_error);
    Py_CLEAR(q->full_error);
#ifdef Py_GIL_DISABLED
    if (q->_queue_inited)
    {
        pthread_mutex_destroy(&(q->lock));
    }
#endif
}


static inline PyObject *_fil_wfifoq_put(FilWFifoQ *q, PyObject *item)
{
    int err;

    Py_INCREF(item);
    if ((err = fil_fifoq_put(&(q->queue), item)))
    {
        Py_DECREF(item);
        if (err == FIL_FIFOQ_ERROR_OUT_OF_MEMORY)
        {
            PyErr_SetString(PyExc_MemoryError, "out of memory inserting queue entry");
            return NULL;
        }
        /* won't reach this due to callers checking 'full' first */
        PyErr_SetNone(q->full_error);
        return NULL;
    }

    fil_waiterlist_signal_first(q->getters);
    Py_RETURN_NONE;
}

#ifndef Py_GIL_DISABLED

/*
 * Stock build.  These four are byte-for-byte what they always were: the GIL is
 * the mutual exclusion, and adding even a no-op lock/unlock plus a result
 * temporary around them costs measurable throughput -- 3.7% on a queue
 * put/get benchmark, on top of the 6.6% the wait-function refactor cost before
 * it was split out.  The free-threading variants live below, separately, so
 * this path never changes shape.
 */
static inline PyObject *fil_wfifoq_put_nowait(FilWFifoQ *q, PyObject *item)
{
    if (fil_wfifoq_full(q))
    {
        PyErr_SetNone(q->full_error);
        return NULL;
    }
    return _fil_wfifoq_put(q, item);
}

static inline PyObject *fil_wfifoq_put(FilWFifoQ *q, PyObject *item, struct timespec *ts)
{
    while(fil_wfifoq_full(q))
    {
        int err = fil_waiterlist_wait(q->putters, ts, q->full_error);

        if (err)
        {
            if (err == FIL_WAITER_SIGNALED_UNWIND)
            {
                /* A get() made room and woke us, and we are unwinding out of
                 * put() with an exception: hand the room to the next putter,
                 * which would otherwise sleep through a queue that is no
                 * longer full. */
                fil_waiterlist_signal_first_keep_exc(q->putters);
            }
            return NULL;
        }
    }

    return _fil_wfifoq_put(q, item);
}

static inline PyObject *fil_wfifoq_get_nowait(FilWFifoQ *q)
{
    void *res;

    if (fil_fifoq_get(&(q->queue), &res))
    {
        PyErr_SetNone(q->empty_error);
        return NULL;
    }

    fil_waiterlist_signal_first(q->putters);
    return res;
}

static inline PyObject *fil_wfifoq_get(FilWFifoQ *q, struct timespec *ts)
{
    while(!q->queue.len)
    {
        int err = fil_waiterlist_wait(q->getters, ts, q->empty_error);

        if (err)
        {
            if (err == FIL_WAITER_SIGNALED_UNWIND)
            {
                /* An item arrived for us and we are unwinding out of get()
                 * with an exception: wake the next getter, which would
                 * otherwise sleep through a queue that is not empty. */
                fil_waiterlist_signal_first_keep_exc(q->getters);
            }
            return NULL;
        }
    }

    return fil_wfifoq_get_nowait(q);
}

#else  /* Py_GIL_DISABLED */

/* Caller holds the queue lock. */
static inline PyObject *_fil_wfifoq_get_locked(FilWFifoQ *q)
{
    void *res;

    if (fil_fifoq_get(&(q->queue), &res))
    {
        PyErr_SetNone(q->empty_error);
        return NULL;
    }

    fil_waiterlist_signal_first(q->putters);
    return res;
}

/*
 * Free-threading build.  Same logic, with the queue lock held across the
 * state test and the decision to wait, and dropped only for the wait itself
 * (inside fil_waiterlist_wait_locked).
 */
static inline PyObject *fil_wfifoq_put_nowait(FilWFifoQ *q, PyObject *item)
{
    PyObject *res;

    FIL_WFIFOQ_LOCK(q);
    if (fil_wfifoq_full(q))
    {
        FIL_WFIFOQ_UNLOCK(q);
        PyErr_SetNone(q->full_error);
        return NULL;
    }
    res = _fil_wfifoq_put(q, item);
    FIL_WFIFOQ_UNLOCK(q);
    return res;
}

static inline PyObject *fil_wfifoq_put(FilWFifoQ *q, PyObject *item, struct timespec *ts)
{
    PyObject *res;

    FIL_WFIFOQ_LOCK(q);
    while(fil_wfifoq_full(q))
    {
        int err = FIL_WFIFOQ_WAIT(q, q->putters, ts, q->full_error);

        if (err)
        {
            if (err == FIL_WAITER_SIGNALED_UNWIND)
            {
                fil_waiterlist_signal_first_keep_exc(q->putters);
            }
            FIL_WFIFOQ_UNLOCK(q);
            return NULL;
        }
    }

    res = _fil_wfifoq_put(q, item);
    FIL_WFIFOQ_UNLOCK(q);
    return res;
}

static inline PyObject *fil_wfifoq_get_nowait(FilWFifoQ *q)
{
    PyObject *res;

    FIL_WFIFOQ_LOCK(q);
    res = _fil_wfifoq_get_locked(q);
    FIL_WFIFOQ_UNLOCK(q);
    return res;
}

static inline PyObject *fil_wfifoq_get(FilWFifoQ *q, struct timespec *ts)
{
    PyObject *res;

    FIL_WFIFOQ_LOCK(q);
    while(!q->queue.len)
    {
        int err = FIL_WFIFOQ_WAIT(q, q->getters, ts, q->empty_error);

        if (err)
        {
            if (err == FIL_WAITER_SIGNALED_UNWIND)
            {
                fil_waiterlist_signal_first_keep_exc(q->getters);
            }
            FIL_WFIFOQ_UNLOCK(q);
            return NULL;
        }
    }

    res = _fil_wfifoq_get_locked(q);
    FIL_WFIFOQ_UNLOCK(q);
    return res;
}

#endif /* Py_GIL_DISABLED */

#endif /* __FIL_CORE_WFIFOQ_H__ */
