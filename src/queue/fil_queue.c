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

    PyObject *deque;
    PyObject *append;
    PyObject *popleft;
    PyObject *len;
    PyObject *tuple_deque_item;

    long queue_size;
    long queue_entries;
    FilWaiterList getters;
    FilWaiterList putters;
} PyFilQueue;

static PyObject *_deque;
static PyObject *_EmptyTuple;
static PyObject *_EmptyError;
static PyObject *_FullError;

static PyFilQueue *_queue_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    PyFilQueue *self = (PyFilQueue *)type->tp_alloc(type, 0);

    if (self != NULL)
    {
        self->deque = NULL;
        self->append = NULL;
        self->popleft = NULL;
        self->len = NULL;
        self->tuple_deque_item = NULL;
        self->queue_size = -1;
        self->queue_entries = 0;
        fil_waiterlist_init(self->getters);
        fil_waiterlist_init(self->putters);
    }

    return self;
}

static int _queue_init(PyFilQueue *self, PyObject *args, PyObject *kwargs)
{
    long maxsize = 0;

    static char *keywords[] = {"maxsize", NULL};
    PyObject *deque = NULL, *append = NULL, *popleft = NULL, *len = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|l",
                                     keywords,
                                     &maxsize))
    {
        return -1;
    }

    if (maxsize <= 0) {
        maxsize = -1;
    }

    self->queue_size = maxsize;

    if (((deque = PyObject_Call(_deque, _EmptyTuple, NULL)) == NULL) ||
        ((append = PyObject_GetAttrString(deque, "append")) == NULL) ||
        ((popleft = PyObject_GetAttrString(deque, "popleft")) == NULL) ||
        ((len = PyObject_GetAttrString(deque, "__len__")) == NULL))
    {
        goto failure;
    }

    if (self->tuple_deque_item == NULL)
    {
        self->tuple_deque_item = PyTuple_New(1);
        if (self->tuple_deque_item == NULL)
        {
            goto failure;
        }
        Py_INCREF(Py_None);
        PyTuple_SET_ITEM(self->tuple_deque_item, 0, Py_None);
    }

    Py_XSETREF(self->deque, deque);
    Py_XSETREF(self->append, append);
    Py_XSETREF(self->popleft, popleft);
    Py_XSETREF(self->len, len);

    return 0;

failure:
    Py_XDECREF(len);
    Py_XDECREF(popleft);
    Py_XDECREF(append);
    Py_XDECREF(deque);

    return -1;
}

static void _queue_dealloc(PyFilQueue *self)
{
    assert(fil_waiterlist_empty(self->getters));
    assert(fil_waiterlist_empty(self->putters));
    Py_XDECREF(self->len);
    Py_XDECREF(self->popleft);
    Py_XDECREF(self->append);
    Py_XDECREF(self->deque);
    Py_XDECREF(self->tuple_deque_item);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

PyDoc_STRVAR(_queue_qsize_doc, "Length of queue.");
static PyObject *_queue_qsize(PyFilQueue *self, PyObject *args)
{
    return PyInt_FromLong(self->queue_entries);
}

PyDoc_STRVAR(_queue_empty_doc, "Is the queue empty?");
static PyObject *_queue_empty(PyFilQueue *self, PyObject *args)
{
    PyObject *res = self->queue_entries ? Py_False : Py_True;
    Py_INCREF(res);
    return res;
}

static inline int __queue_full(PyFilQueue *self)
{
    /* full if 1 more entry would overflow the count */
    if (1 + self->queue_entries < self->queue_entries) {
        return 1;
    }
    else if (self->queue_size == -1)
    {
        return 0;
    }
    return self->queue_entries >= self->queue_size ? 1 : 0;
}

PyDoc_STRVAR(_queue_full_doc, "Is the queue full?");
static PyObject *_queue_full(PyFilQueue *self, PyObject *args)
{
    PyObject *res = __queue_full(self) ? Py_True : Py_False;
    Py_INCREF(res);
    return res;
}

PyDoc_STRVAR(_queue_get_nowait_doc, "Get from queue without blocking.");
static PyObject *_queue_get_nowait(PyFilQueue *self, PyObject *args)
{
    PyObject *res;
   
    res = PyObject_Call(self->popleft, _EmptyTuple, NULL);
    if (res == NULL)
    {
        PyObject *pt, *pv, *ptb;
        PyErr_Fetch(&pt, &pv, &ptb);
        if (!pt || PyErr_GivenExceptionMatches(pt, PyExc_IndexError))
        {
            Py_XDECREF(pt);
            Py_XDECREF(pv);
            Py_XDECREF(ptb);
            PyErr_SetNone(_EmptyError);
            return NULL;
        }
        PyErr_Restore(pt, pv, ptb);
        return NULL;
    }

    self->queue_entries--;
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
        return _queue_get_nowait(self, NULL);
    }

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    while(!self->queue_entries)
    {
        err = fil_waiterlist_wait(self->getters, ts);
        if (err)
        {
            PyObject *pt, *pv, *ptb;
            PyErr_Fetch(&pt, &pv, &ptb);
            if (!pt || PyErr_GivenExceptionMatches(pt, PyFil_TimeoutExc))
            {
                Py_XDECREF(pt);
                Py_XDECREF(pv);
                Py_XDECREF(ptb);
                PyErr_SetNone(_EmptyError);
            }
            else
            {
                PyErr_Restore(pt, pv, ptb);
            }
            return NULL;
        }
    }

    return _queue_get_nowait(self, NULL);
}

static inline int __queue_put(PyFilQueue *self, PyObject *item)
{
    PyObject *res;

    Py_INCREF(item);
    if (PyTuple_SetItem(self->tuple_deque_item, 0, item) == -1)
    {
        /* reference is removed even on error */
        return -1;
    }
   
    res = PyObject_Call(self->append, self->tuple_deque_item, NULL);
    if (res == NULL)
    {
        return -1;
    }

    Py_DECREF(res);

    self->queue_entries++;
    fil_waiterlist_signal_first(self->getters);

    Py_INCREF(Py_None);
    PyTuple_SetItem(self->tuple_deque_item, 0, Py_None);
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
        err = fil_waiterlist_wait(self->putters, ts);
        if (err)
        {
            PyObject *pt, *pv, *ptb;
            PyErr_Fetch(&pt, &pv, &ptb);
            if (!pt || PyErr_GivenExceptionMatches(pt, PyFil_TimeoutExc))
            {
                Py_XDECREF(pt);
                Py_XDECREF(pv);
                Py_XDECREF(ptb);
                PyErr_SetNone(_EmptyError);
            }
            else
            {
                PyErr_Restore(pt, pv, ptb);
            }
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
    return self->queue_entries;
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
    Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE,     /* tp_flags */
    0,                                          /* tp_doc */
    0,                                          /* tp_traverse */
    0,                                          /* tp_clear */
    0,                                          /* tp_richcompare */
    0,                                          /* tp_weaklistoffset */
    0,                                          /* tp_iter */
    0,                                          /* tp_iternext */
    _queue_methods,                             /* tp_methods */
    0,
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
    PyObject *cm = NULL;
    PyObject *qm = NULL;

    PyGreenlet_Import();
    if (PyFilCore_Import() < 0)
    {
        return;
    }

    _EmptyTuple = fil_empty_tuple();

    m = Py_InitModule3("_filament.queue", _fil_queue_module_methods, _fil_queue_module_doc);
    if (m == NULL)
    {
        return;
    }

    if (PyType_Ready(&_queue_type) < 0)
    {
        return;
    }

    if ((cm = PyImport_ImportModuleNoBlock("_collections")) == NULL)
    {
        goto failure;
    }

    if ((_deque = PyObject_GetAttrString(cm, "deque")) == NULL)
    {
        goto failure;
    }

    Py_CLEAR(cm);

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
    Py_CLEAR(_deque);
    Py_XDECREF(qm);
    Py_XDECREF(cm);

    return;
}
