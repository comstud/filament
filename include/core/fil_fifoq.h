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

static int _fil_fifoq_freelist_len, _fil_fifoq_chunk_freelist_len;
static FilFifoQ *_fil_fifoq_freelist[FIL_FIFOQ_FREELIST_SIZE];
static FilFifoQChunk *_fil_fifoq_chunk_freelist[FIL_FIFOQ_CHUNK_FREELIST_SIZE];

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
    if (_fil_fifoq_chunk_freelist_len)
    {
        return _fil_fifoq_chunk_freelist[--_fil_fifoq_chunk_freelist_len];
    }
    return malloc(FIL_FIFOQ_CHUNK_OBJ_SIZE);
}

static inline void _fil_fifoq_chunk_free(FilFifoQChunk *chunk)
{
    if (_fil_fifoq_chunk_freelist_len == FIL_FIFOQ_CHUNK_FREELIST_SIZE - 1)
    {
        free(chunk);
    }
    else
    {
        _fil_fifoq_chunk_freelist[_fil_fifoq_chunk_freelist_len++] = chunk;
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
    FilFifoQ *q;

    if (_fil_fifoq_freelist_len)
    {
        q = _fil_fifoq_freelist[--_fil_fifoq_freelist_len];
    }
    else
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
    if (_fil_fifoq_freelist_len == FIL_FIFOQ_FREELIST_SIZE - 1)
    {
        fil_fifoq_deinit(q);
        free(q);
        return;
    }

    q->len = 0;
    _fil_fifoq_freelist[_fil_fifoq_freelist_len++] = q;

    return;
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
