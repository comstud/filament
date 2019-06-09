/* 
 * The MIT License (MIT): http://opensource.org/licenses/mit-license.php
 * 
 * Copyright (c) 2013-2014, Chris Behrens
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
#include <greenlet.h>
#include <errno.h>
#include "fil_scheduler.h"
#include "fil_timer.h"
#include "fil_util.h"
#include "fil_exceptions.h"


typedef struct _pyfil_timer {
    PyObject_HEAD
#define FIL_TIMER_FLAGS_CANCELLED  0x00000001
    uint32_t flags;
    PyObject *func;
    PyObject *args;
    PyObject *kwargs;
} PyFilTimer;

typedef struct _pyfil_localtimer {
    PyFilTimer timer;
    PyGreenlet *src_gl;
} PyFilLocalTimer;


static void _timer_callback(PyFilScheduler *sched, PyFilTimer *timer)
{
    if (!(timer->flags & FIL_TIMER_FLAGS_CANCELLED))
    {
        PyObject *result;

        result = PyObject_Call(timer->func, timer->args, timer->kwargs);
        Py_XDECREF(result);
        Py_CLEAR(timer->func);
        Py_CLEAR(timer->args);
        Py_CLEAR(timer->kwargs);
    }
    Py_DECREF(timer);
}

static PyFilTimer *_timer_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    PyFilTimer *self = NULL;

    self = (PyFilTimer *)type->tp_alloc(type, 0);
    if (self == NULL)
        return NULL;
    return self;
}

static int _timer_init(PyFilTimer *self, PyObject *args, PyObject *kwargs)
{
    Py_ssize_t args_len;
    PyObject *method;
    PyObject *method_args;
    PyObject *timeout;
    struct timespec tsbuf;
    struct timespec *ts;
    PyFilScheduler *sched;
    int err;

    args_len = PyTuple_GET_SIZE(args);
    if (args_len < 2)
    {
        PyErr_SetString(PyExc_TypeError, "Timer() takes at least 2 arguments");
        return -1;
    }

    timeout = PyTuple_GET_ITEM(args, 0);

    if ((err = fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts)) < 0)
    {
        return -1;
    }

    /*
     * go ahead and create a scheduler, if we need to. Timer doesn't
     * work without one.
     */
    sched = fil_scheduler_get(1);
    if (sched == NULL)
    {
        return -1;
    }

    method = PyTuple_GET_ITEM(args, 1);
    if (!PyCallable_Check(method))
    {
        PyErr_SetString(PyExc_TypeError, "Timer() 2nd argument should be a callable");
        return -1;
    }

    if (self->func != NULL)
    {
        PyErr_SetString(PyExc_TypeError, "Timer() already initialized");
        return -1;
    }

    method_args = PyTuple_GetSlice(args, 2, args_len);
    if (method_args == NULL)
    {
        return -1;
    }

    Py_INCREF(method);
    self->func = method;
    self->args = method_args;
    Py_XINCREF(kwargs);
    self->kwargs = kwargs;

    Py_INCREF(self);

    err = fil_scheduler_add_event(sched, ts, 0,
                                  (fil_event_cb_t)_timer_callback, self);
    if (err)
    {
        Py_CLEAR(self->func);
        Py_CLEAR(self->args);
        Py_CLEAR(self->kwargs);
        Py_DECREF(self);
        return -1;
    }

    return 0;
}

static void _timer_dealloc(PyFilTimer *self)
{
    Py_CLEAR(self->func);
    Py_CLEAR(self->args);
    Py_CLEAR(self->kwargs);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

PyDoc_STRVAR(_timer_cancel_doc, "Cancel the timer.");
static PyObject *_timer_cancel(PyFilTimer *self, PyObject *args)
{
    self->flags |= FIL_TIMER_FLAGS_CANCELLED;
    Py_RETURN_NONE;
}

static PyMethodDef _timer_methods[] = {
    {"cancel", (PyCFunction)_timer_cancel, METH_VARARGS|METH_KEYWORDS, _timer_cancel_doc},
    { NULL, NULL }
};

static PyTypeObject _timer_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "filament.timer.Timer",                     /* tp_name */
    sizeof(PyFilTimer),                         /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_timer_dealloc,                 /* tp_dealloc */
    0,                                          /* tp_print */
    0,                                          /* tp_getattr */
    0,                                          /* tp_setattr */
    0,                                          /* tp_compare */
    0,                                          /* tp_repr */
    0,                                          /* tp_as_number */
    0,                                          /* tp_as_sequence */
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
    _timer_methods,                             /* tp_methods */
    0,
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_timer_init,                      /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_timer_new,                        /* tp_new */
    PyObject_Del,                               /* tp_free */
};


/****************/

int fil_timer_type_init(PyObject *module)
{
    PyObject *m;

    PyGreenlet_Import();
    if (PyType_Ready(&_timer_type) < 0)
        return -1;

    m = fil_create_module("filament.timer");
    if (m == NULL)
        return -1;

    Py_INCREF((PyObject *)&_timer_type);
    if (PyModule_AddObject(m, "Timer", (PyObject *)&_timer_type) != 0)
    {
        Py_DECREF((PyObject *)&_timer_type);
        Py_DECREF(m);
        return -1;
    }

    if (PyModule_AddObject(module, "timer", m) != 0)
    {
        Py_DECREF(m);
        return -1;
    }

    return 0;
}
