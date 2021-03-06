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
#include "locking/fil_lock.h"

typedef struct _pyfil_lock {
    PyObject_HEAD
    int locked;
    FilWaiterList waiters;
} PyFilLock;

typedef struct _pyfil_rlock {
    PyFilLock lock; /* must remain first. */
    uint64_t owner;
    uint64_t count;
} PyFilRLock;


static PyFilLock *_lock_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    PyFilLock *self = (PyFilLock *)type->tp_alloc(type, 0);

    if (self != NULL)
    {
        fil_waiterlist_init(self->waiters);
    }

    return self;
}

static int _lock_init(PyFilLock *self, PyObject *args, PyObject *kwargs)
{
    return 0;
}

static void _lock_dealloc(PyFilLock *self)
{
    assert(fil_waiterlist_empty(self->waiters));

    PyObject_Del(self);
}

static int __lock_acquire(PyFilLock *lock, int blocking, struct timespec *ts)
{
    if (!lock->locked && fil_waiterlist_empty(lock->waiters))
    {
        lock->locked = 1;
        return 0;
    }

    if (!blocking)
    {
        return 1;
    }

    int err = fil_waiterlist_wait(lock->waiters, ts, NULL);
    if (err < 0)
    {
        return err;
    }

    assert(lock->locked == 1);

    return 0;
}

static int __lock_release(PyFilLock *lock)
{
    if (!lock->locked)
    {
        PyErr_SetString(PyExc_RuntimeError, "release without acquire");
        return -1;
    }

    if (fil_waiterlist_empty(lock->waiters))
    {
        lock->locked = 0;
        return 0;
    }

    /* leave 'locked' set because a different thread is just
     * going to grab it anyway.  This prevents some races without
     * additional work to resolve them.
     */
    fil_waiterlist_signal_first(lock->waiters);
    return 0;
}

static int __rlock_acquire(PyFilRLock *lock, int blocking, struct timespec *ts)
{
    uint64_t owner;

    owner = fil_get_ident();
    if (!lock->lock.locked && fil_waiterlist_empty(lock->lock.waiters))
    {
        lock->lock.locked = 1;
        lock->owner = owner;
        lock->count = 1;
        return 0;
    }

    if (owner == lock->owner)
    {
        lock->count++;
        return 0;
    }

    if (!blocking)
    {
        return 1;
    }

    int err = fil_waiterlist_wait(lock->lock.waiters, ts, NULL);
    if (err)
    {
        return err;
    }

    assert(lock->lock.locked == 1);
    lock->owner = owner;
    lock->count = 1;

    return 0;
}

static int __rlock_release(PyFilRLock *lock)
{
    if (!lock->lock.locked || (fil_get_ident() != lock->owner))
    {
        PyErr_SetString(PyExc_RuntimeError, "cannot release un-acquired lock");
        return -1;
    }

    if (--lock->count > 0)
    {
        return 0;
    }

    lock->owner = 0;

    if (fil_waiterlist_empty(lock->lock.waiters))
    {
        lock->lock.locked = 0;
        return 0;
    }

    /* leave 'locked' set because a different thread is just
     * going to grab it anyway.  This prevents some races without
     * additional work to resolve them.
     */
    fil_waiterlist_signal_first(lock->lock.waiters);
    return 0;
}

PyDoc_STRVAR(_lock_acquire_doc, "Acquire the lock.");
static PyObject *_lock_acquire(PyFilLock *self, PyObject *args, PyObject *kwargs)
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
    err = __lock_acquire(self, blocking, ts);
    if (err < 0 && err != -ETIMEDOUT)
    {
        return NULL;
    }

    if (err == 0)
    {
        Py_INCREF(Py_True);
        return Py_True;
    }

    Py_INCREF(Py_False);
    return Py_False;
}

PyDoc_STRVAR(_lock_locked_doc, "Is the lock locked?");
static PyObject *_lock_locked(PyFilLock *self)
{
    PyObject *res = (self->locked || !fil_waiterlist_empty(self->waiters)) ? Py_True : Py_False;
    Py_INCREF(res);
    return res;
}

PyDoc_STRVAR(_lock_release_doc, "Release the lock.");
static PyObject *_lock_release(PyFilLock *self)
{
    if (__lock_release(self) < 0)
    {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *_lock_enter(PyFilLock *self)
{
    int err = __lock_acquire(self, 1, NULL);
    if (err)
    {
        if (!PyErr_Occurred())
        {
            PyErr_Format(PyExc_RuntimeError, "unexpected failure in Lock.__enter__: %d", err);
        }
        return NULL;
    }

    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *_lock_exit(PyFilLock *self, PyObject *args)
{
    return _lock_release(self);
}

PyDoc_STRVAR(_rlock_acquire_doc, "Acquire the lock.");
static PyObject *_rlock_acquire(PyFilRLock *self, PyObject *args, PyObject *kwargs)
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
    err = __rlock_acquire(self, blocking, ts);
    if (err < 0 && err != -ETIMEDOUT)
    {
        return NULL;
    }

    if (err == 0)
    {
        Py_INCREF(Py_True);
        return Py_True;
    }

    Py_INCREF(Py_False);
    return Py_False;
}

PyDoc_STRVAR(_rlock_locked_doc, "Is the lock locked (by someone else)?");
static PyObject *_rlock_locked(PyFilRLock *self)
{
    uint64_t owner = fil_get_ident();
    PyObject *res = ((self->lock.locked && self->owner != owner) ||
            !fil_waiterlist_empty(self->lock.waiters)) ? Py_True : Py_False;
    Py_INCREF(res);
    return res;
}

PyDoc_STRVAR(_rlock_release_doc, "Release the lock.");
static PyObject *_rlock_release(PyFilRLock *self)
{
    if (__rlock_release(self) < 0)
    {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *_rlock_enter(PyFilRLock *self)
{
    int err = __rlock_acquire(self, 1, NULL);
    if (err)
    {
        if (!PyErr_Occurred())
        {
            PyErr_Format(PyExc_RuntimeError, "unexpected failure in RLock.__enter__: %d", err);
        }
        return NULL;
    }

    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *_rlock_exit(PyFilRLock *self, PyObject *args)
{
    return _rlock_release(self);
}


static PyMethodDef _lock_methods[] = {
    { "acquire", (PyCFunction)_lock_acquire, METH_VARARGS|METH_KEYWORDS, _lock_acquire_doc },
    { "release", (PyCFunction)_lock_release, METH_NOARGS, _lock_release_doc },
    { "locked", (PyCFunction)_lock_locked, METH_NOARGS, _lock_locked_doc },
    { "__enter__", (PyCFunction)_lock_enter, METH_NOARGS, NULL },
    { "__exit__", (PyCFunction)_lock_exit, METH_VARARGS, NULL },
    { NULL, NULL }
};

static PyMethodDef _rlock_methods[] = {
    { "acquire", (PyCFunction)_rlock_acquire, METH_VARARGS|METH_KEYWORDS, _rlock_acquire_doc },
    { "release", (PyCFunction)_rlock_release, METH_NOARGS, _rlock_release_doc },
    { "locked", (PyCFunction)_rlock_locked, METH_NOARGS, _rlock_locked_doc },
    { "__enter__", (PyCFunction)_rlock_enter, METH_NOARGS, NULL },
    { "__exit__", (PyCFunction)_rlock_exit, METH_VARARGS, NULL },
    { NULL, NULL }
};

static PyTypeObject _lock_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "_filament.locking.Lock",                 /* tp_name */
    sizeof(PyFilLock),                           /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_lock_dealloc,                  /* tp_dealloc */
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
    _lock_methods,                              /* tp_methods */
    0,                                          /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_lock_init,                       /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_lock_new,                         /* tp_new */
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

/* Re-entrant lock.  We can use the same calls here */
static PyTypeObject _rlock_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "_filament.locking.RLock",                  /* tp_name */
    sizeof(PyFilRLock),                         /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_lock_dealloc,                  /* tp_dealloc */
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
    _rlock_methods,                             /* tp_methods */
    0,                                          /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_lock_init,                       /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_lock_new,                         /* tp_new */
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

PyFilLock *fil_lock_alloc(void)
{
    return _lock_new(&_lock_type, NULL, NULL);
}

PyFilRLock *fil_rlock_alloc(void)
{
    return (PyFilRLock *)_lock_new(&_rlock_type, NULL, NULL);
}

int fil_lock_acquire(PyFilLock *lock, int blocking, struct timespec *ts)
{
    return __lock_acquire(lock, blocking, ts);
}

int fil_rlock_acquire(PyFilRLock *rlock, int blocking, struct timespec *ts)
{
    return __rlock_acquire(rlock, blocking, ts);
}

int fil_lock_release(PyFilLock *lock)
{
    return __lock_release(lock);
}

int fil_rlock_release(PyFilRLock *rlock)
{
    return __rlock_release(rlock);
}

int fil_lock_type_init(PyObject *module)
{
    PyFilCore_Import();

    if (PyType_Ready(&_lock_type) < 0)
    {
        return -1;
    }
    if (PyType_Ready(&_rlock_type) < 0)
    {
        return -1;
    }

    Py_INCREF((PyObject *)&_lock_type);
    if (PyModule_AddObject(module, "Lock", (PyObject *)&_lock_type) != 0)
    {
        Py_DECREF((PyObject *)&_lock_type);
        return -1;
    }

    Py_INCREF((PyObject *)&_rlock_type);
    if (PyModule_AddObject(module, "RLock", (PyObject *)&_rlock_type) != 0)
    {
        Py_DECREF((PyObject *)&_rlock_type);
        return -1;
    }

    return 0;
}
