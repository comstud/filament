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
#ifndef __FILAMENT_QUEUE_FIL_QUEUE_H__
#define __FILAMENT_QUEUE_FIL_QUEUE_H__

#include "core/filament.h"

typedef struct _pyfil_simple_queue {
    PyObject_HEAD
    FilWFifoQ queue;
} PyFilSimpleQueue;

typedef struct _pyfil_queue {
    PyObject_HEAD
    FilWFifoQ queue;
    uint64_t unfinished_tasks;
    FilWaiterList task_done_waiters;
} PyFilQueue;

#ifdef __FIL_BUILDING_QUEUE__

extern PyObject *_FIL_QUEUE_EMPTY_ERROR, *_FIL_QUEUE_FULL_ERROR;
extern PyTypeObject *_FIL_QUEUE_TYPE, *_FIL_SIMPLE_QUEUE_TYPE;

int fil_queue_init(PyObject *m);
int fil_simplequeue_init(PyObject *m);

#endif

#endif /* __FILAMENT_QUEUE_FIL_QUEUE_H__ */
