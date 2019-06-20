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

typedef struct _fil_wfifoq {
    int _queue_inited;
    uint64_t max_size;
    FilFifoQ queue;

    FilWaiterList getters;
    FilWaiterList putters;
} FilWFifoQ;

static PyObject *_FIL_WFIFOQ_EMPTY_ERROR;
static PyObject *_FIL_WFIFOQ_FULL_ERROR;

#define fil_wfifoq_len(__q) ((__q)->queue.len)
#define fil_wfifoq_empty(__q) (fil_wfifoq_len(__q) == 0)
/*
 * q->queue.len and q->max_size are both uint64_t and max_size
 * is set to (uint64_t)-1 if unlimited, so this simple check works fine:
 */
#define fil_wfifoq_full(__q) ((__q)->queue.len >= (__q)->max_size)

static inline int fil_wfifoq_init(FilWFifoQ *q, uint64_t max_size)
{
    if (_FIL_WFIFOQ_EMPTY_ERROR == NULL)
    {
        PyObject *qm = PyImport_ImportModuleNoBlock("Queue");
        if (qm == NULL)
        {
            return -1;
        }

        _FIL_WFIFOQ_EMPTY_ERROR = PyObject_GetAttrString(qm, "Empty");
        if (_FIL_WFIFOQ_EMPTY_ERROR == NULL)
        {
            Py_DECREF(qm);
            return -1;
        }

        _FIL_WFIFOQ_FULL_ERROR = PyObject_GetAttrString(qm, "Full");
        if (_FIL_WFIFOQ_FULL_ERROR == NULL)
        {
            Py_CLEAR(_FIL_WFIFOQ_EMPTY_ERROR);
            Py_DECREF(qm);
            return -1;
        }

        Py_DECREF(qm);
    }

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
    fil_waiterlist_init(q->getters);
    fil_waiterlist_init(q->putters);
    return 0;
}

static inline void fil_wfifoq_deinit(FilWFifoQ *q)
{
    assert(fil_waiterlist_empty(q->getters));
    assert(fil_waiterlist_empty(q->putters));
    if (q->_queue_inited)
    {
        fil_fifoq_deinit(&(q->queue));
    }
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
        PyErr_SetNone(_FIL_WFIFOQ_FULL_ERROR);
        return NULL;
    }

    fil_waiterlist_signal_first(q->getters);
    Py_RETURN_NONE;
}

static inline PyObject *fil_wfifoq_put_nowait(FilWFifoQ *q, PyObject *item)
{
    if (fil_wfifoq_full(q))
    {
        PyErr_SetNone(_FIL_WFIFOQ_FULL_ERROR);
        return NULL;
    }
    return _fil_wfifoq_put(q, item);
}

static inline PyObject *fil_wfifoq_put(FilWFifoQ *q, PyObject *item, struct timespec *ts)
{
    while(fil_wfifoq_full(q))
    {
        if (fil_waiterlist_wait(q->putters, ts, _FIL_WFIFOQ_FULL_ERROR))
        {
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
        PyErr_SetNone(_FIL_WFIFOQ_EMPTY_ERROR);
        return NULL;
    }

    fil_waiterlist_signal_first(q->putters);
    return res;
}

static inline PyObject *fil_wfifoq_get(FilWFifoQ *q, struct timespec *ts)
{
    while(!q->queue.len)
    {
        if (fil_waiterlist_wait(q->getters, ts, _FIL_WFIFOQ_EMPTY_ERROR))
        {
            return NULL;
        }
    }

    return fil_wfifoq_get_nowait(q);
}

#endif /* __FIL_CORE_WFIFOQ_H__ */
