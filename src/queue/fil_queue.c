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

#define __FIL_BUILDING_QUEUE__
#include "queue/fil_queue.h"

PyTypeObject *_FIL_QUEUE_TYPE = NULL;

static PyFilQueue *_queue_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    PyFilQueue *self;

    static char *keywords[] = {"maxsize", NULL};
    long maxsize = -1;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|l:Queue",
                                     keywords,
                                     &maxsize))
    {
        return NULL;
    }

    if ((self = (PyFilQueue *)type->tp_alloc(type, 0)) != NULL)
    {
        if (maxsize < 0)
        {
            maxsize = 0;
        }

        if (fil_wfifoq_init(&(self->queue), maxsize, _FIL_QUEUE_EMPTY_ERROR, _FIL_QUEUE_FULL_ERROR))
        {
            Py_DECREF(self);
            return NULL;
        }

        fil_waiterlist_init(self->task_done_waiters);
    }

    return self;
}

static int _queue_init(PyFilQueue *self, PyObject *args, PyObject *kwargs)
{
    return 0;
}

static void _queue_dealloc(PyFilQueue *self)
{
    fil_wfifoq_deinit(&(self->queue));
    PyObject_Del(self);
}

PyDoc_STRVAR(_queue_qsize_doc, "Length of queue.");
static PyObject *_queue_qsize(PyFilQueue *self, PyObject *args)
{
    return PyInt_FromLong(fil_wfifoq_len(&(self->queue)));
}

PyDoc_STRVAR(_queue_empty_doc, "Is the queue empty?");
static PyObject *_queue_empty(PyFilQueue *self, PyObject *args)
{
    PyObject *res = fil_wfifoq_empty(&(self->queue)) ? Py_True : Py_False;
    Py_INCREF(res);
    return res;
}

PyDoc_STRVAR(_queue_full_doc, "Is the queue full?");
static PyObject *_queue_full(PyFilQueue *self, PyObject *args)
{
    PyObject *res = fil_wfifoq_full(&(self->queue)) ? Py_True : Py_False;
    Py_INCREF(res);
    return res;
}

PyDoc_STRVAR(_queue_get_nowait_doc, "Get from queue without blocking.");
static PyObject *_queue_get_nowait(PyFilQueue *self)
{
    return fil_wfifoq_get_nowait(&(self->queue));
}

PyDoc_STRVAR(_queue_get_doc, "Get from queue.");
static PyObject *_queue_get_common(PyFilQueue *self, PyObject *block, PyObject *timeout)
{
    double timeout_dbl = 0;
    struct timespec tsbuf, *ts = NULL;

    if (block == NULL || PyObject_IsTrue(block))
    {
        if (fil_double_from_timeout_obj(timeout, &timeout_dbl))
        {
            return NULL;
        }
    }

    if (timeout_dbl == 0)
    {
        return fil_wfifoq_get_nowait(&(self->queue));
    }

    if (fil_timespec_from_double_interval(timeout_dbl, &tsbuf, &ts))
    {
        return NULL;
    }

    return fil_wfifoq_get(&(self->queue), ts);
}

#ifdef _FIL_PYTHON3
static PyObject *_queue_get(PyFilQueue *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    static const char * const keywords[] = {"block", "timeout"};
    PyObject *argv[2];

    if (fil_fastcall_parse(args, nargs, kwnames, "get",
                           0, 2, keywords, argv) < 0)
    {
        return NULL;
    }

    return _queue_get_common(self, argv[0], argv[1]);
}
#else
static PyObject *_queue_get(PyFilQueue *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"block", "timeout", NULL};
    PyObject *block = NULL, *timeout = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO",
                                     keywords,
                                     &block,
                                     &timeout))
    {
        return NULL;
    }

    return _queue_get_common(self, block, timeout);
}
#endif

PyDoc_STRVAR(_queue_put_nowait_doc, "Put into queue.");
static PyObject *_queue_put_nowait(PyFilQueue *self, PyObject *item)
{
    PyObject *res = fil_wfifoq_put_nowait(&(self->queue), item);
    if (res != NULL)
    {
        self->unfinished_tasks++;
    }
    return res;
}

PyDoc_STRVAR(_queue_put_doc, "Put into queue.");
static PyObject *_queue_put_common(PyFilQueue *self, PyObject *item, PyObject *block, PyObject *timeout)
{
    PyObject *res;
    double timeout_dbl = 0;
    struct timespec tsbuf, *ts = NULL;

    if (block == NULL || PyObject_IsTrue(block))
    {
        if (fil_double_from_timeout_obj(timeout, &timeout_dbl))
        {
            return NULL;
        }
    }

    if (timeout_dbl == 0)
    {
        return fil_wfifoq_put_nowait(&(self->queue), item);
    }

    if (fil_timespec_from_double_interval(timeout_dbl, &tsbuf, &ts))
    {
        return NULL;
    }

    res = fil_wfifoq_put(&(self->queue), item, ts);
    if (res != NULL)
    {
        self->unfinished_tasks++;
    }
    return res;
}

#ifdef _FIL_PYTHON3
static PyObject *_queue_put(PyFilQueue *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    static const char * const keywords[] = {"item", "block", "timeout"};
    PyObject *argv[3];

    if (fil_fastcall_parse(args, nargs, kwnames, "put",
                           1, 3, keywords, argv) < 0)
    {
        return NULL;
    }

    return _queue_put_common(self, argv[0], argv[1], argv[2]);
}
#else
static PyObject *_queue_put(PyFilQueue *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"item", "block", "timeout", NULL};
    PyObject *item, *block = NULL, *timeout = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OO",
                                     keywords,
                                     &item,
                                     &block,
                                     &timeout))
    {
        return NULL;
    }

    return _queue_put_common(self, item, block, timeout);
}
#endif

PyDoc_STRVAR(_queue_task_done_doc,
"Indicate that a formerly enqueued task is complete.\n\
\n\
Used by Queue consumer threads.  For each get() used to fetch a task,\n\
a subsequent call to task_done() tells the queue that the processing\n\
on the task is complete.\n\
\n\
If a join() is currently blocking, it will resume when all items\n\
have been processed (meaning that a task_done() call was received\n\
for every item that had been put() into the queue).\n\
\n\
Raises a ValueError if called more times than there were items\n\
placed in the queue.\n");
static PyObject *_queue_task_done(PyFilQueue *self)
{
    if (self->unfinished_tasks == 0)
    {
        PyErr_SetString(PyExc_ValueError, "task_done() called too many times");
        return NULL;
    }

    self->unfinished_tasks--;
    fil_waiterlist_signal_all(self->task_done_waiters);

    Py_RETURN_NONE;
}

PyDoc_STRVAR(_queue_join_doc,
"Blocks until all items in the Queue have been gotten and processed.\n\
\n\
The count of unfinished tasks goes up whenever an item is added to the\n\
queue. The count goes down whenever a consumer thread calls task_done()\n\
to indicate the item was retrieved and all work on it is complete.\n\
\n\
When the count of unfinished tasks drops to zero, join() unblocks.\n\
\n\
FILAMENT EXTENSION:\n\
\n\
A 'timeout' keyword argument can be specified to only wait a specific\n\
period of time. It may be None to wait forever, or a number >= 0 representing\n\
number of seconds to wait. floats are accepted. If the time expires before\n\
the tasks are done, False will be returned. The normal return value is None.\n");
static PyObject *_queue_join(PyFilQueue *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"timeout", NULL};
    PyObject *timeout = NULL;
    struct timespec tsbuf, *ts = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O",
                                     keywords,
                                     &timeout))
    {
        return NULL;
    }

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    while (self->unfinished_tasks)
    {
        /* We use full error just to distinguish a timeout from
         * a different type of exception.
         */
        if (fil_waiterlist_wait(self->task_done_waiters, ts, _FIL_QUEUE_FULL_ERROR))
        {
            PyObject *exc_type, *exc_value, *exc_tb;

            PyErr_Fetch(&exc_type, &exc_value, &exc_tb);
            if (!PyErr_GivenExceptionMatches(exc_type, _FIL_QUEUE_FULL_ERROR))
            {
                PyErr_Restore(exc_type, exc_value, exc_tb);
                return NULL;
            }

            Py_DECREF(exc_type);
            Py_XDECREF(exc_value);
            Py_XDECREF(exc_tb);

            Py_RETURN_FALSE;
        }
    }
    Py_RETURN_NONE;
}

static PyMethodDef _queue_methods[] = {
#ifdef _FIL_PYTHON3
    { "get", (PyCFunction)(void (*)(void))_queue_get, METH_FASTCALL|METH_KEYWORDS, _queue_get_doc },
#else
    { "get", (PyCFunction)_queue_get, METH_VARARGS|METH_KEYWORDS, _queue_get_doc },
#endif
    { "get_nowait", (PyCFunction)_queue_get_nowait, METH_NOARGS, _queue_get_nowait_doc },
#ifdef _FIL_PYTHON3
    { "put", (PyCFunction)(void (*)(void))_queue_put, METH_FASTCALL|METH_KEYWORDS, _queue_put_doc },
#else
    { "put", (PyCFunction)_queue_put, METH_VARARGS|METH_KEYWORDS, _queue_put_doc },
#endif
    { "put_nowait", (PyCFunction)_queue_put_nowait, METH_O, _queue_put_nowait_doc },
    { "qsize", (PyCFunction)_queue_qsize, METH_NOARGS, _queue_qsize_doc },
    { "empty", (PyCFunction)_queue_empty, METH_NOARGS, _queue_empty_doc },
    { "full", (PyCFunction)_queue_full, METH_NOARGS, _queue_full_doc },
    { "task_done", (PyCFunction)_queue_task_done, METH_NOARGS, _queue_task_done_doc },
    { "join", (PyCFunction)_queue_join, METH_VARARGS|METH_KEYWORDS, _queue_join_doc },
    { NULL, NULL }
};

static Py_ssize_t _queue_len(PyFilQueue *self)
{
    return fil_wfifoq_len(&(self->queue));
}

static PySequenceMethods _queue_as_sequence = {
    (lenfunc)_queue_len,                        /* sq_length */
    0,                                          /*sq_concat*/
    0,                                          /*sq_repeat*/
    0,                                          /*sq_item*/
    0,                                          /*sq_slice*/
    0,                                          /*sq_ass_item*/
    0,                                          /*sq_ass_slice*/
};

static PyTypeObject _queue_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "filament.queue.Queue",                     /* tp_name */
    sizeof(PyFilQueue),                         /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_queue_dealloc,                 /* tp_dealloc */
    0,                                          /* tp_print */
    0,                                          /* tp_getattr */
    0,                                          /* tp_setattr */
    0,                                          /* tp_compare */
    0,                                          /* tp_repr */
    0,                                          /* tp_as_number */
    &_queue_as_sequence,                        /* tp_as_sequence */
    0,                                          /* tp_as_mapping */
    0,                                          /* tp_hash */
    0,                                          /* tp_call */
    0,                                          /* tp_str */
    PyObject_GenericGetAttr,                    /* tp_getattro */
    0,                                          /* tp_setattro */
    0,                                          /* tp_as_buffer */
    FIL_DEFAULT_TPFLAGS,                        /* tp_flags */
    0,                                          /* tp_doc */
    0,                                          /* tp_traverse */
    0,                                          /* tp_clear */
    0,                                          /* tp_richcompare */
    0,                                          /* tp_weaklistoffset */
    0,                                          /* tp_iter */
    0,                                          /* tp_iternext */
    _queue_methods,                             /* tp_methods */
    0,                                          /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_queue_init,                      /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_queue_new,                        /* tp_new */
    PyObject_Del,                               /* tp_free */
    0,                                          /* tp_is_gc */
    0,                                          /* tp_bases */
    0,                                          /* tp_mro */
    0,                                          /* tp_cache */
    0,                                          /* tp_subclasses */
    0,                                          /* tp_weaklist */
    0,                                          /* tp_del */
    0,                                          /* tp_version_tag */
};

/****************/

int fil_queue_init(PyObject *m)
{
    if (PyFilCore_Import() < 0)
    {
        return -1;
    }

    if (PyType_Ready(&_queue_type) < 0)
    {
        return -1;
    }

    _FIL_QUEUE_TYPE = &_queue_type;

    Py_INCREF((PyObject *)&_queue_type);
    if (PyModule_AddObject(m, "Queue",
                           (PyObject *)&_queue_type) != 0)
    {
        Py_DECREF((PyObject *)&_queue_type);
        return -1;
    }

    return 0;
}
