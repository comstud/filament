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
#ifndef __FIL_CORE_FIFOQ_H__
#define __FIL_CORE_FIFOQ_H__

#include "core/filament.h"

#ifndef FIL_FIFOQ_TARGET_CHUNK_SIZE
#define FIL_FIFOQ_TARGET_CHUNK_SIZE 8192
#endif

#define _FIL_FIFOQ_CIO offsetof(FilFifoQChunk, items)
#define _FIL_FIFOQ_CTS (sizeof(void *) * FIL_FIFOQ_TARGET_CHUNK_SIZE)
#define _FIL_FIFOQ_ALIGN(__x, __y) (((__x) + (__y) - 1) & ~((__y)-1))

/* make the Chunk object size a multiple of 8192 */
#define FIL_FIFOQ_CHUNK_SIZE        ((_FIL_FIFOQ_ALIGN(_FIL_FIFOQ_CIO + _FIL_FIFOQ_CTS, 8192) - _FIL_FIFOQ_CIO) / sizeof(void *))
#define FIL_FIFOQ_CHUNK_OBJ_SIZE    (_FIL_FIFOQ_CIO + (sizeof(void *) * FIL_FIFOQ_CHUNK_SIZE))

#ifndef FIL_FIFOQ_CHUNK_SIZE
#define FIL_FIFOQ_CHUNK_SIZE 8192
#endif

#ifndef FIL_FIFOQ_FREELIST_SIZE
#define FIL_FIFOQ_FREELIST_SIZE 16
#endif

#ifndef FIL_FIFOQ_CHUNK_FREELIST_SIZE
#define FIL_FIFOQ_CHUNK_FREELIST_SIZE 128
#endif

#define FIL_FIFOQ_ERROR_EMPTY -1
#define FIL_FIFOQ_ERROR_OUT_OF_MEMORY -2
#define FIL_FIFOQ_ERROR_OUT_OF_ROOM -3

typedef struct _fil_fifoq_chunk FilFifoQChunk;
typedef struct _fil_fifoq FilFifoQ;

/*
 * Freelists: per-translation-unit statics, serialized by the GIL on a normal
 * build exactly like the waiter freelist (see fil_waiter.h) -- every
 * put/get/init/deinit runs GIL-held there, and FIL_FIFOQ_FL_LOCK compiles to
 * nothing.
 *
 * On a FREE-THREADING build (PEP 703) there is no GIL, and unlike the queue
 * state itself these statics are shared BETWEEN queues: two Queue objects on
 * two OS threads, each correctly holding its own queue lock, still race here
 * -- two allocs can pop the same chunk (both queues then scribble PyObject
 * pointers into one block) and two frees can both pass the bounds test and
 * push past the end of the array.  There the freelists get their own mutex.
 * A mutex and not the waiter freelist's per-thread TLS treatment because the
 * trade is reversed: waiters are the hottest allocation in the scheduler
 * (every park), while a chunk changes hands once per FIL_FIFOQ_CHUNK_SIZE
 * (8192) queue operations -- far too cold for a lock to matter -- and chunks
 * are ~64KB each, so per-thread pools of up to 128 of them would also trade
 * a lock for a multi-MB-per-thread memory hazard.
 */
static int _fil_fifoq_freelist_len, _fil_fifoq_chunk_freelist_len;
static FilFifoQ *_fil_fifoq_freelist[FIL_FIFOQ_FREELIST_SIZE];
static FilFifoQChunk *_fil_fifoq_chunk_freelist[FIL_FIFOQ_CHUNK_FREELIST_SIZE];

#ifdef Py_GIL_DISABLED
static pthread_mutex_t _fil_fifoq_freelist_lock = PTHREAD_MUTEX_INITIALIZER;
#  define FIL_FIFOQ_FL_LOCK()   pthread_mutex_lock(&_fil_fifoq_freelist_lock)
#  define FIL_FIFOQ_FL_UNLOCK() pthread_mutex_unlock(&_fil_fifoq_freelist_lock)
#else
#  define FIL_FIFOQ_FL_LOCK()   ((void)0)
#  define FIL_FIFOQ_FL_UNLOCK() ((void)0)
#endif

struct _fil_fifoq_chunk
{
    uint64_t append_idx;
    FilFifoQChunk *next_chunk;
    void *items[1]; /* void *items[FIL_FIFOQ_CHUNK_SIZE]; */
};

struct _fil_fifoq {
    FilFifoQChunk *head;
    FilFifoQChunk *tail;
    uint64_t pop_idx;
    uint64_t len;
};

static inline FilFifoQChunk *_fil_fifoq_chunk_alloc(void)
{
    FilFifoQChunk *chunk = NULL;

    FIL_FIFOQ_FL_LOCK();
    if (_fil_fifoq_chunk_freelist_len)
    {
        chunk = _fil_fifoq_chunk_freelist[--_fil_fifoq_chunk_freelist_len];
    }
    FIL_FIFOQ_FL_UNLOCK();

    if (chunk != NULL)
    {
        return chunk;
    }
    return malloc(FIL_FIFOQ_CHUNK_OBJ_SIZE);
}

static inline void _fil_fifoq_chunk_free(FilFifoQChunk *chunk)
{
    FIL_FIFOQ_FL_LOCK();
    if (_fil_fifoq_chunk_freelist_len < FIL_FIFOQ_CHUNK_FREELIST_SIZE - 1)
    {
        _fil_fifoq_chunk_freelist[_fil_fifoq_chunk_freelist_len++] = chunk;
        chunk = NULL;
    }
    FIL_FIFOQ_FL_UNLOCK();

    if (chunk != NULL)
    {
        free(chunk);
    }
}

/* for when statically allocated */
static inline int fil_fifoq_init(FilFifoQ *q)
{
    if ((q->head = q->tail = _fil_fifoq_chunk_alloc()) == NULL)
    {
        return -1;
    }
#ifndef NDEBUG
    q->head->next_chunk = NULL;
#endif
    q->len = 0;
    q->pop_idx = 0;
    q->head->append_idx = -1;
    return 0;
}

static inline FilFifoQ *fil_fifoq_alloc(void)
{
    FilFifoQ *q = NULL;

    FIL_FIFOQ_FL_LOCK();
    if (_fil_fifoq_freelist_len)
    {
        q = _fil_fifoq_freelist[--_fil_fifoq_freelist_len];
    }
    FIL_FIFOQ_FL_UNLOCK();

    if (q == NULL)
    {
        q = malloc(sizeof(FilFifoQ));
        if (q == NULL)
        {
            return NULL;
        }
        if (fil_fifoq_init(q))
        {
            free(q);
            return NULL;
        }
        return q;
    }
    assert(q->head == q->tail);
    assert(q->len == 0);
    q->head->append_idx = -1;
    q->pop_idx = 0;
    return q;
}

static inline void _fil_fifoq_dump(FilFifoQ *q)
{
    FilFifoQChunk *head;

    while ((head = q->head) != q->tail)
    {
        q->head = head->next_chunk;
        _fil_fifoq_chunk_free(head);
    }
}

/* for when statically allocated -- do not call unless _init() succeeded! */
static inline void fil_fifoq_deinit(FilFifoQ *q)
{
    _fil_fifoq_dump(q);
    _fil_fifoq_chunk_free(q->head);
    q->head = NULL;
    q->len = 0;
}

static inline void fil_fifoq_free(FilFifoQ *q)
{
    FIL_FIFOQ_FL_LOCK();
    if (_fil_fifoq_freelist_len < FIL_FIFOQ_FREELIST_SIZE - 1)
    {
        q->len = 0;
        _fil_fifoq_freelist[_fil_fifoq_freelist_len++] = q;
        q = NULL;
    }
    FIL_FIFOQ_FL_UNLOCK();

    if (q != NULL)
    {
        fil_fifoq_deinit(q);
        free(q);
    }
}

static inline int fil_fifoq_put(FilFifoQ *q, void *item)
{
    FilFifoQChunk *tail = q->tail;

    if (q->len + 1 < q->len)
    {
        return FIL_FIFOQ_ERROR_OUT_OF_ROOM;
    }

    if (tail->append_idx == (FIL_FIFOQ_CHUNK_SIZE - 1))
    {
        if ((tail->next_chunk = _fil_fifoq_chunk_alloc()) == NULL)
        {
            return FIL_FIFOQ_ERROR_OUT_OF_MEMORY;
        }
        tail = q->tail = tail->next_chunk;
#ifndef NDEBUG
        tail->next_chunk = NULL;
#endif
        tail->append_idx = -1;
    }

    tail->items[++tail->append_idx] = item;
    ++q->len;
    return 0;
}

static inline int fil_fifoq_get(FilFifoQ *q, void **item_ret)
{
    FilFifoQChunk *head = q->head;

    if (q->len == 0)
    {
        return FIL_FIFOQ_ERROR_EMPTY;
    }

    *item_ret = head->items[q->pop_idx];

    if (--q->len == 0)
    {
        assert(head == q->tail);
        q->pop_idx = 0;
        head->append_idx = -1;
        return 0;
    }

    if (++q->pop_idx == FIL_FIFOQ_CHUNK_SIZE)
    {
        q->head = head->next_chunk;
        assert(q->head != NULL);
        _fil_fifoq_chunk_free(head);
        q->pop_idx = 0;
    }

    return 0;
}

#endif /* __FIL_CORE_FIFOQ_H__ */
