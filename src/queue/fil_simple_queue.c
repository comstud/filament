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

PyTypeObject *_FIL_SIMPLE_QUEUE_TYPE = NULL;

static PyFilSimpleQueue *_queue_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    PyFilSimpleQueue *self;

    static char *keywords[] = {NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, ":SimpleQueue", keywords))
    {
        return NULL;
    }

    if ((self = (PyFilSimpleQueue *)type->tp_alloc(type, 0)) != NULL)
    {
        if (fil_wfifoq_init(&(self->queue), 0, _FIL_QUEUE_EMPTY_ERROR, _FIL_QUEUE_FULL_ERROR))
        {
            Py_DECREF(self);
            return NULL;
        }
    }

    return self;
}

static int _queue_init(PyFilSimpleQueue *self, PyObject *args, PyObject *kwargs)
{
    return 0;
}

static void _queue_dealloc(PyFilSimpleQueue *self)
{
    fil_wfifoq_deinit(&(self->queue));
    PyObject_Del(self);
}

PyDoc_STRVAR(_queue_qsize_doc, "Length of queue.");
static PyObject *_queue_qsize(PyFilSimpleQueue *self, PyObject *args)
{
    return PyInt_FromLong(fil_wfifoq_len(&(self->queue)));
}

PyDoc_STRVAR(_queue_empty_doc, "Is the queue empty?");
static PyObject *_queue_empty(PyFilSimpleQueue *self, PyObject *args)
{
    PyObject *res = fil_wfifoq_empty(&(self->queue)) ? Py_True : Py_False;
    Py_INCREF(res);
    return res;
}

PyDoc_STRVAR(_queue_get_nowait_doc, "Get from queue without blocking.");
static PyObject *_queue_get_nowait(PyFilSimpleQueue *self)
{
    return fil_wfifoq_get_nowait(&(self->queue));
}

PyDoc_STRVAR(_queue_get_doc, "Get from queue.");
static PyObject *_queue_get(PyFilSimpleQueue *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"block", "timeout", NULL};
    PyObject *block = NULL, *timeout = NULL;
    double timeout_dbl = 0;
    struct timespec tsbuf, *ts = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO",
                                     keywords,
                                     &block,
                                     &timeout))
    {
        return NULL;
    }

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

PyDoc_STRVAR(_queue_put_nowait_doc, "Put into queue.");
static PyObject *_queue_put_nowait(PyFilSimpleQueue *self, PyObject *item)
{
    return fil_wfifoq_put_nowait(&(self->queue), item);
}

PyDoc_STRVAR(_queue_put_doc, "Put into queue.");
static PyObject *_queue_put(PyFilSimpleQueue *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"item", "block", "timeout", NULL};
    PyObject *item, *block = NULL, *timeout = NULL;
    double timeout_dbl = 0;
    struct timespec tsbuf, *ts = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OO",
                                     keywords,
                                     &item,
                                     &block,
                                     &timeout))
    {
        return NULL;
    }

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

    return fil_wfifoq_put(&(self->queue), item, ts);
}

static PyMethodDef _queue_methods[] = {
    { "get", (PyCFunction)_queue_get, METH_VARARGS|METH_KEYWORDS, _queue_get_doc },
    { "get_nowait", (PyCFunction)_queue_get_nowait, METH_NOARGS, _queue_get_nowait_doc },
    { "put", (PyCFunction)_queue_put, METH_VARARGS|METH_KEYWORDS, _queue_put_doc },
    { "put_nowait", (PyCFunction)_queue_put_nowait, METH_O, _queue_put_nowait_doc },
    { "qsize", (PyCFunction)_queue_qsize, METH_NOARGS, _queue_qsize_doc },
    { "empty", (PyCFunction)_queue_empty, METH_NOARGS, _queue_empty_doc },
    { NULL, NULL }
};

static Py_ssize_t _queue_len(PyFilSimpleQueue *self)
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
    "filament.queue.SimpleQueue",               /* tp_name */
    sizeof(PyFilSimpleQueue),                   /* tp_basicsize */
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

int fil_simplequeue_init(PyObject *m)
{
    if (PyFilCore_Import() < 0)
    {
        return -1;
    }

    if (PyType_Ready(&_queue_type) < 0)
    {
        return -1;
    }

    _FIL_SIMPLE_QUEUE_TYPE = &_queue_type;

    Py_INCREF((PyObject *)&_queue_type);
    if (PyModule_AddObject(m, "SimpleQueue",
                           (PyObject *)&_queue_type) != 0)
    {
        Py_DECREF((PyObject *)&_queue_type);
        return -1;
    }

    return 0;
}
