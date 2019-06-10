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

#define __FIL_BUILDING_LOCKING__
#include "core/filament.h"
#include "locking/fil_semaphore.h"

typedef struct _pyfil_semaphore {
    PyObject_HEAD
    Py_ssize_t counter;
    FilWaiterList waiters;
} PyFilSemaphore;

static PyFilSemaphore *_semaphore_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    PyFilSemaphore *self = (PyFilSemaphore *)type->tp_alloc(type, 0);

    if (self != NULL)
    {
        fil_waiterlist_init(self->waiters);
        self->counter = 1;
    }

    return self;
}

static void _semaphore_dealloc(PyFilSemaphore *self)
{
    Py_TYPE(self)->tp_free((PyObject *)self);
    assert(fil_waiterlist_empty(self->waiters));
}

static int _semaphore_init(PyFilSemaphore *self, PyObject *args, PyObject *kwargs)
{
    PyObject *value = NULL;

    static char *keywords[] = {"value", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O",
                                     keywords,
                                     &value))
    {
        return -1;
    }

    if (value == NULL)
    {
        return 0;
    }

    if (!PyInt_Check(value) && !PyLong_Check(value))
    {
        PyErr_SetString(PyExc_TypeError, "value must be an int or long");
        return -1;
    }

    self->counter = PyInt_AsSsize_t(value);
    if (PyErr_Occurred())
        return -1;

    return 0;
}

static int __semaphore_acquire(PyFilSemaphore *sema, int blocking, struct timespec *ts)
{
    /* If there are waiters, we should let them acquire before we do */
    if ((sema->counter > 0) && fil_waiterlist_empty(sema->waiters))
    {
        sema->counter--;
        return 0;
    }

    if (!blocking)
    {
        return EAGAIN;
    }

    if (fil_waiterlist_wait(sema->waiters, ts)) {
        return -1;
    }

    return 0;
}

static void __semaphore_release(PyFilSemaphore *sema)
{
    if (sema->counter < 0)
    {
        sema->counter++;
        return;
    }

    if (fil_waiterlist_empty(sema->waiters))
    {
        sema->counter++;
        return;
    }

    /* leave 'counter' decremented because a different thread is
     * just going to grab it anyway. This prevents some races without
     * additional work to resolve them.
     */
    fil_waiterlist_signal_first(sema->waiters);

    return;
}

PyDoc_STRVAR(_semaphore_acquire_doc, "Acquire the semaphore.");
static PyObject *_semaphore_acquire(PyFilSemaphore *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"blocking", "timeout", NULL};
    PyObject *blockingobj = NULL;
    PyObject *timeout = NULL;
    struct timespec tsbuf;
    struct timespec *ts;
    int blocking;
    int err;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O!O",
                                     keywords,
                                     &PyBool_Type,
                                     &blockingobj, &timeout))
    {
        return NULL;
    }

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    blocking = (blockingobj == NULL || blockingobj == Py_True);
    err = __semaphore_acquire(self, blocking, ts);
    if (err)
    {
        return NULL;
    }

    Py_RETURN_NONE;
}

PyDoc_STRVAR(_semaphore_release_doc, "Release the semaphore.");
static PyObject *_semaphore_release(PyFilSemaphore *self, PyObject *args)
{
    __semaphore_release(self);
    Py_RETURN_NONE;
}

static PyMethodDef _semaphore_methods[] = {
    {"acquire", (PyCFunction)_semaphore_acquire, METH_VARARGS|METH_KEYWORDS, _semaphore_acquire_doc},
    {"release", (PyCFunction)_semaphore_release, METH_NOARGS, _semaphore_release_doc},
    { NULL, NULL }
};

static PyTypeObject _semaphore_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "filament.semaphore.Semaphore",             /* tp_name */
    sizeof(PyFilSemaphore),                      /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_semaphore_dealloc,             /* tp_dealloc */
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
    _semaphore_methods,                         /* tp_methods */
    0,                                          /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_semaphore_init,                  /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_semaphore_new,                    /* tp_new */
    PyObject_Del,                               /* tp_free */
};


/****************/

int fil_semaphore_type_init(PyObject *module)
{
    PyFilCore_Import();

    if (PyType_Ready(&_semaphore_type) < 0)
    {
        return -1;
    }

    Py_INCREF((PyObject *)&_semaphore_type);
    if (PyModule_AddObject(module, "Semaphore",
                           (PyObject *)&_semaphore_type) != 0)
    {
        Py_DECREF((PyObject *)&_semaphore_type);
        return -1;
    }

    return 0;
}
