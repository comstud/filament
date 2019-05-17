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
#include "fil_waiter.h"
#include "fil_util.h"
#include "fil_exceptions.h"

static PyFilWaiter *_waiter_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    PyFilWaiter *self = NULL;

    self = (PyFilWaiter *)type->tp_alloc(type, 0);
    if (self == NULL)
        return NULL;

    pthread_mutex_init(&(self->waiter_lock), NULL);
    pthread_cond_init(&(self->waiter_cond), NULL);

    return self;
}

static int _waiter_init(PyFilWaiter *self, PyObject *args, PyObject *kwargs)
{
    return 0;
}

static void _waiter_dealloc(PyFilWaiter *self)
{
    Py_CLEAR(self->gl);
    pthread_mutex_destroy(&(self->waiter_lock));
    pthread_cond_destroy(&(self->waiter_cond));
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static void _handle_timeout(PyFilScheduler *sched, PyFilWaiter *waiter)
{
    if (waiter->signaled)
    {
        Py_DECREF(waiter);
        return;
    }
    fil_scheduler_gl_switch(sched, NULL, waiter->gl);
    Py_DECREF(waiter);
}

static int __waiter_wait(PyFilWaiter *waiter, struct timespec *ts)
{
    if (waiter->signaled)
    {
        return 0;
    }

    waiter->sched = fil_scheduler_get(0);
    if (waiter->sched == NULL)
    {
        PyThreadState *thr_state;
        int err;

        while(!waiter->signaled)
        {
            thr_state = PyEval_SaveThread();
            pthread_mutex_lock(&(waiter->waiter_lock));
            /* race with GIL unlocked */
            if (waiter->signaled)
            {
                pthread_mutex_unlock(&(waiter->waiter_lock));
                PyEval_RestoreThread(thr_state);
                break;
            }
            if (ts)
            {
                err = pthread_cond_timedwait(&(waiter->waiter_cond),
                                     &(waiter->waiter_lock), ts);
                if (err == ETIMEDOUT || err == ETIME)
                {
                    pthread_mutex_unlock(&(waiter->waiter_lock));
                    PyEval_RestoreThread(thr_state);
                    /* Check for race */
                    if (waiter->signaled)
                        break;
                    PyErr_SetString(PyFil_TimeoutExc,
                                    "Wait timed out");
                    return 1;
                }
            }
            else
            {
                pthread_cond_wait(&(waiter->waiter_cond),
                                  &(waiter->waiter_lock));
            }
            pthread_mutex_unlock(&(waiter->waiter_lock));
            PyEval_RestoreThread(thr_state);
        }

        return 0;
    }

    waiter->gl = PyGreenlet_GetCurrent();

    if (ts != NULL)
    {
        Py_INCREF(waiter);
        fil_scheduler_add_event(waiter->sched, ts, 0,
                                (fil_event_cb_t)_handle_timeout, waiter);
    }

    fil_scheduler_switch(waiter->sched);

    Py_CLEAR(waiter->gl);

    if (!(waiter->signaled))
    {
        if (PyErr_Occurred())
        {
            return -1;
        }

        /* must be a timeout */
        PyErr_SetString(PyFil_TimeoutExc, "Wait timed out");

        return 1;
    }

    return 0;
}

static void __waiter_signal(PyFilWaiter *waiter)
{
    if (waiter->signaled)
        return;

    waiter->signaled = 1;

    if (waiter->sched == NULL)
    {
        /* We don't necessarily need to release the GIL but this
         * might be better to wake up other threads sooner
         */
        PyThreadState *thr_state = PyEval_SaveThread();

        pthread_mutex_lock(&(waiter->waiter_lock));
        pthread_cond_signal(&(waiter->waiter_cond));
        pthread_mutex_unlock(&(waiter->waiter_lock));

        PyEval_RestoreThread(thr_state);
        return;
    }

    if (waiter->gl != NULL)
    {
        fil_scheduler_gl_switch(waiter->sched, NULL, waiter->gl);
    }

    return;
}

PyDoc_STRVAR(_waiter_wait_doc, "Wait for signal.");
static PyObject *_waiter_wait(PyFilWaiter *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"timeout", NULL};
    PyObject *timeout = NULL;
    struct timespec tsbuf;
    struct timespec *ts;
    int err;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O",
                                     keywords,
                                     &timeout))
    {
        return NULL;
    }

    if (fil_timeoutobj_to_timespec(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    err = __waiter_wait(self, ts);
    if (err)
        return NULL;
    Py_RETURN_NONE;
}

PyDoc_STRVAR(_waiter_signal_doc, "Signal the waiter.");
static PyObject *_waiter_signal(PyFilWaiter *self, PyObject *args)
{
    __waiter_signal(self);
    Py_RETURN_NONE;
}

static PyMethodDef _waiter_methods[] = {
    {"wait", (PyCFunction)_waiter_wait, METH_VARARGS|METH_KEYWORDS, _waiter_wait_doc},
    {"signal", (PyCFunction)_waiter_signal, METH_NOARGS, _waiter_signal_doc},
    { NULL, NULL }
};

static PyTypeObject _waiter_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "filament.waiter.Waiter",                   /* tp_name */
    sizeof(PyFilWaiter),                        /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_waiter_dealloc,                /* tp_dealloc */
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
    _waiter_methods,                            /* tp_methods */
    0,
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_waiter_init,                     /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_waiter_new,                       /* tp_new */
    PyObject_Del,                               /* tp_free */
};


/****************/

int fil_waiter_type_init(PyObject *module)
{
    PyObject *m;

    PyGreenlet_Import();
    if (PyType_Ready(&_waiter_type) < 0)
        return -1;

    m = fil_create_module("filament.waiter");
    if (m == NULL)
        return -1;

    Py_INCREF((PyObject *)&_waiter_type);
    if (PyModule_AddObject(m, "Waiter", (PyObject *)&_waiter_type) != 0)
    {
        Py_DECREF((PyObject *)&_waiter_type);
        return -1;
    }

    if (PyModule_AddObject(module, "waiter", m) != 0)
    {
        return -1;
    }

    Py_INCREF(m);

    return 0;
}

PyFilWaiter *fil_waiter_alloc(void)
{
    return _waiter_new(&_waiter_type, NULL, NULL);
}

int fil_waiter_wait(PyFilWaiter *waiter, struct timespec *ts)
{
    return __waiter_wait(waiter, ts);
}

void fil_waiter_signal(PyFilWaiter *waiter)
{
    __waiter_signal(waiter);
}
