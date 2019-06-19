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

#include <Python.h>

#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <pthread.h>
#include <errno.h>
#include "core/filament.h"
#include "core/fil_util.h"
#include "core/fil_waiter.h"

typedef struct _pyfil_queue {
    PyObject_HEAD

    FilFifoQ *queue;
    uint64_t queue_max_size;

    FilWaiterList getters;
    FilWaiterList putters;
} PyFilQueue;

static PyObject *_EmptyError;
static PyObject *_FullError;

static PyFilQueue *_queue_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    PyFilQueue *self;

    static char *keywords[] = {"maxsize", NULL};
    long maxsize = -1;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|l",
                                     keywords,
                                     &maxsize))
    {
        return NULL;
    }

    if ((self = (PyFilQueue *)type->tp_alloc(type, 0)) != NULL)
    {
        if ((self->queue = fil_fifoq_alloc()) == NULL)
        {
            PyErr_SetString(PyExc_MemoryError, "out of memory allocating queue");
            Py_DECREF(self);
            return NULL;
        }
        self->queue_max_size = maxsize < 0 ? 0 : maxsize;
        fil_waiterlist_init(self->getters);
        fil_waiterlist_init(self->putters);
    }

    return self;
}

static int _queue_init(PyFilQueue *self, PyObject *args, PyObject *kwargs)
{
    return 0;
}

static void _queue_dealloc(PyFilQueue *self)
{
    assert(fil_waiterlist_empty(self->getters));
    assert(fil_waiterlist_empty(self->putters));
    if (self->queue != NULL)
    {
        fil_fifoq_free(self->queue);
    }
    PyObject_Del(self);
}

PyDoc_STRVAR(_queue_qsize_doc, "Length of queue.");
static PyObject *_queue_qsize(PyFilQueue *self, PyObject *args)
{
    return PyInt_FromLong(self->queue->len);
}

PyDoc_STRVAR(_queue_empty_doc, "Is the queue empty?");
static PyObject *_queue_empty(PyFilQueue *self, PyObject *args)
{
    PyObject *res = self->queue->len ? Py_False : Py_True;
    Py_INCREF(res);
    return res;
}

static inline int __queue_full(PyFilQueue *self)
{
    if (1 + self->queue->len < self->queue->len)
    {
        return 1;
    }
    else if (self->queue_max_size == 0)
    {
        return 0;
    }
    return self->queue->len < self->queue_max_size ? 0 : 1;
}

PyDoc_STRVAR(_queue_full_doc, "Is the queue full?");
static PyObject *_queue_full(PyFilQueue *self, PyObject *args)
{
    PyObject *res = __queue_full(self) ? Py_True : Py_False;
    Py_INCREF(res);
    return res;
}

PyDoc_STRVAR(_queue_get_nowait_doc, "Get from queue without blocking.");
static PyObject *_queue_get_nowait(PyFilQueue *self)
{
    void *res;

    if (fil_fifoq_get(self->queue, &res))
    {
        PyErr_SetNone(_EmptyError);
        return NULL;
    }

    fil_waiterlist_signal_first(self->putters);

    return res;
}

PyDoc_STRVAR(_queue_get_doc, "Get from queue.");
static PyObject *_queue_get(PyFilQueue *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"block", "timeout", NULL};
    PyObject *block = NULL, *timeout = NULL;
    int err;
    struct timespec tsbuf, *ts = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO",
                                     keywords,
                                     &block,
                                     &timeout))
    {
        return NULL;
    }

    if (block != NULL && PyObject_Not(block))
    {
        return _queue_get_nowait(self);
    }

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    while(!self->queue->len)
    {
        err = fil_waiterlist_wait(self->getters, ts, _EmptyError);
        if (err)
        {
            return NULL;
        }
    }

    return _queue_get_nowait(self);
}

static inline int __queue_put(PyFilQueue *self, PyObject *item)
{
    int err;

    Py_INCREF(item);
    if ((err = fil_fifoq_put(self->queue, item)))
    {
        if (err == FIL_FIFOQ_ERROR_OUT_OF_MEMORY)
        {
            PyErr_SetString(PyExc_MemoryError, "out of memory inserting entry");
            return -1;
        }
        /* shouldn't reach this because we check fullness before all calls to this */
        PyErr_SetNone(_FullError);
        return -1;
    }

    fil_waiterlist_signal_first(self->getters);

    return 0;
}

PyDoc_STRVAR(_queue_put_nowait_doc, "Put into queue.");
static PyObject *_queue_put_nowait(PyFilQueue *self, PyObject *args)
{
    PyObject *item;

    if (!PyArg_ParseTuple(args, "O", &item))
    {
        return NULL;
    }

    if (__queue_full(self))
    {
        PyErr_SetNone(_FullError);
        return NULL;
    }

    if (__queue_put(self, item) < 0)
    {
        return NULL;
    }

    Py_RETURN_NONE;
}

PyDoc_STRVAR(_queue_put_doc, "Put into queue.");
static PyObject *_queue_put(PyFilQueue *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"item", "block", "timeout", NULL};
    PyObject *item = NULL, *block = NULL, *timeout = NULL;
    int err;
    struct timespec tsbuf, *ts = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OO",
                                     keywords,
                                     &item,
                                     &block,
                                     &timeout))
    {
        return NULL;
    }

    if (block != NULL && PyObject_Not(block))
    {
        if (__queue_full(self)) {
            PyErr_SetNone(_FullError);
            return NULL;
        }
        if (__queue_put(self, item) < 0)
        {
            return NULL;
        }
        Py_RETURN_NONE;
    }

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    while(__queue_full(self))
    {
        err = fil_waiterlist_wait(self->putters, ts, _FullError);
        if (err)
        {
            return NULL;
        }
    }

    if (__queue_put(self, item) < 0)
    {
        return NULL;
    }

    Py_RETURN_NONE;
}

static PyMethodDef _queue_methods[] = {
    {"get", (PyCFunction)_queue_get, METH_VARARGS|METH_KEYWORDS, _queue_get_doc},
    {"get_nowait", (PyCFunction)_queue_get_nowait, METH_NOARGS, _queue_get_nowait_doc},
    {"put", (PyCFunction)_queue_put, METH_VARARGS|METH_KEYWORDS, _queue_put_doc},
    {"put_nowait", (PyCFunction)_queue_put_nowait, METH_VARARGS, _queue_put_nowait_doc},
    {"qsize", (PyCFunction)_queue_qsize, METH_NOARGS, _queue_qsize_doc},
    {"empty", (PyCFunction)_queue_empty, METH_NOARGS, _queue_empty_doc},
    {"full", (PyCFunction)_queue_full, METH_NOARGS, _queue_full_doc},
    /*
    {"join", (PyCFunction)_queue_join, METH_NOARGS, _queue_join_doc},
    {"task_done", (PyCFunction)_queue_task_done, METH_NOARGS, _queue_task_done_doc},
    */
    { NULL, NULL }
};

static Py_ssize_t _queue_len(PyFilQueue *self)
{
    return self->queue->len;
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

PyDoc_STRVAR(_fil_queue_module_doc, "Filament _filament module.");
static PyMethodDef _fil_queue_module_methods[] = {
    { NULL, },
};

/****************/

PyMODINIT_FUNC
initqueue(void)
{
    PyObject *m = NULL;
    PyObject *qm = NULL;

    PyGreenlet_Import();
    if (PyFilCore_Import() < 0)
    {
        return;
    }

    m = Py_InitModule3("_filament.queue", _fil_queue_module_methods, _fil_queue_module_doc);
    if (m == NULL)
    {
        return;
    }

    if (PyType_Ready(&_queue_type) < 0)
    {
        return;
    }

    if ((qm = PyImport_ImportModuleNoBlock("Queue")) == NULL)
    {
        goto failure;
    }

    _EmptyError = PyObject_GetAttrString(qm, "Empty");
    _FullError = PyObject_GetAttrString(qm, "Full");

    Py_CLEAR(qm);

    if (_EmptyError == NULL || _FullError == NULL)
    {
        goto failure;
    }

    Py_INCREF(_EmptyError);
    if (PyModule_AddObject(m, "Empty", _EmptyError) < 0)
    {
        Py_DECREF(_EmptyError);
        goto failure;
    }

    Py_INCREF(_FullError);
    if (PyModule_AddObject(m, "Full", _FullError) < 0)
    {
        Py_DECREF(_FullError);
        goto failure;
    }

    Py_INCREF((PyObject *)&_queue_type);
    if (PyModule_AddObject(m, "Queue",
                           (PyObject *)&_queue_type) != 0)
    {
        Py_DECREF((PyObject *)&_queue_type);
        goto failure;
    }

    return;

failure:

    Py_CLEAR(_EmptyError);
    Py_CLEAR(_FullError);
    Py_XDECREF(qm);

    return;
}
