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
    assert(fil_waiterlist_empty(self->waiters));

    /* Respect tp_free: Python subclass instances are GC-allocated, and
     * PyObject_Del on them frees the wrong pointer (heap corruption). */
    Py_TYPE(self)->tp_free((PyObject *)self);
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

static void __semaphore_release(PyFilSemaphore *sema);

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

    /* Preserve the error code (-ETIMEDOUT vs other) so acquire() can report
     * a timeout by returning False like Lock/RLock do.
     */
    int err = fil_waiterlist_wait(sema->waiters, ts, NULL);
    if (err < 0)
    {
        return err;
    }

    if (err == FIL_WAITER_SIGNALED_UNWIND)
    {
        /* The count release() decremented for us is ours, and we are leaving
         * with an exception: give it back (to the next waiter if there is
         * one) or the semaphore loses a permit permanently.  Signaling can
         * raise, so keep the exception we are propagating out of its way. */
        PyObject *exc_type, *exc_value, *exc_tb;

        PyErr_Fetch(&exc_type, &exc_value, &exc_tb);
        __semaphore_release(sema);
        PyErr_Restore(exc_type, exc_value, exc_tb);
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
static PyObject *_semaphore_acquire_common(PyFilSemaphore *self, PyObject *blockingobj, PyObject *timeout)
{
    struct timespec tsbuf;
    struct timespec *ts;
    int blocking;
    int err;

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    blocking = (blockingobj == NULL || blockingobj == Py_True);
    err = __semaphore_acquire(self, blocking, ts);
    if (err < 0 && err != -ETIMEDOUT)
    {
        return NULL;
    }

    if (err == 0)
    {
        Py_RETURN_TRUE;
    }

    /*
     * EAGAIN (non-blocking, unavailable) or -ETIMEDOUT: acquire() reports
     * failure by returning False, never by raising -- matching Lock/RLock,
     * gevent, and the stdlib.  On timeout, fil_waiterlist_wait() left an
     * exc.Timeout pending; clear it or CPython raises SystemError.
     */
    if (err == -ETIMEDOUT)
    {
        PyErr_Clear();
    }

    Py_RETURN_FALSE;
}

#ifdef _FIL_PYTHON3
static PyObject *_semaphore_acquire(PyFilSemaphore *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    static const char * const keywords[] = {"blocking", "timeout"};
    PyObject *argv[2];

    if (fil_fastcall_parse(args, nargs, kwnames, "acquire",
                           0, 2, keywords, argv) < 0)
    {
        return NULL;
    }

    if (argv[0] != NULL && !PyBool_Check(argv[0]))
    {
        PyErr_SetString(PyExc_TypeError,
                        "acquire() argument 'blocking' must be bool");
        return NULL;
    }

    return _semaphore_acquire_common(self, argv[0], argv[1]);
}
#else
static PyObject *_semaphore_acquire(PyFilSemaphore *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"blocking", "timeout", NULL};
    PyObject *blockingobj = NULL;
    PyObject *timeout = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O!O",
                                     keywords,
                                     &PyBool_Type,
                                     &blockingobj, &timeout))
    {
        return NULL;
    }

    return _semaphore_acquire_common(self, blockingobj, timeout);
}
#endif

PyDoc_STRVAR(_semaphore_release_doc, "Release the semaphore.  Returns the new counter (gevent parity).");
static PyObject *_semaphore_release(PyFilSemaphore *self, PyObject *args)
{
    __semaphore_release(self);
    return PyInt_FromSsize_t(self->counter);
}

PyDoc_STRVAR(_semaphore_locked_doc, "True if the semaphore cannot be acquired immediately.");
static PyObject *_semaphore_locked(PyFilSemaphore *self)
{
    PyObject *res = (self->counter <= 0) ? Py_True : Py_False;
    Py_INCREF(res);
    return res;
}

static PyObject *_semaphore_enter(PyFilSemaphore *self)
{
    int err = __semaphore_acquire(self, 1, NULL);
    if (err)
    {
        if (!PyErr_Occurred())
        {
            PyErr_Format(PyExc_RuntimeError, "unexpected failure in Semaphore.__enter__: %d", err);
        }
        return NULL;
    }

    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *_semaphore_exit(PyFilSemaphore *self, PyObject *args)
{
    __semaphore_release(self);
    Py_RETURN_NONE;
}

static PyMethodDef _semaphore_methods[] = {
#ifdef _FIL_PYTHON3
    {"acquire", (PyCFunction)(void (*)(void))_semaphore_acquire, METH_FASTCALL|METH_KEYWORDS, _semaphore_acquire_doc},
#else
    {"acquire", (PyCFunction)_semaphore_acquire, METH_VARARGS|METH_KEYWORDS, _semaphore_acquire_doc},
#endif
    {"release", (PyCFunction)_semaphore_release, METH_NOARGS, _semaphore_release_doc},
    {"locked", (PyCFunction)_semaphore_locked, METH_NOARGS, _semaphore_locked_doc},
    {"__enter__", (PyCFunction)_semaphore_enter, METH_NOARGS, NULL},
    {"__exit__", (PyCFunction)_semaphore_exit, METH_VARARGS, NULL},
    { NULL, NULL }
};

static PyMemberDef _semaphore_memberlist[] = {
    { "counter", T_PYSSIZET, offsetof(PyFilSemaphore, counter), READONLY, "current semaphore counter" },
    { NULL, },
};

static PyTypeObject _semaphore_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "_filament.locking.Semaphore",              /* tp_name */
    sizeof(PyFilSemaphore),                     /* tp_basicsize */
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
    FIL_DEFAULT_TPFLAGS,                        /* tp_flags */
    0,                                          /* tp_doc */
    0,                                          /* tp_traverse */
    0,                                          /* tp_clear */
    0,                                          /* tp_richcompare */
    0,                                          /* tp_weaklistoffset */
    0,                                          /* tp_iter */
    0,                                          /* tp_iternext */
    _semaphore_methods,                         /* tp_methods */
    _semaphore_memberlist,                      /* tp_members */
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
