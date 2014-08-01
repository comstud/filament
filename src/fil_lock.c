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
#include "fil_lock.h"
#include "fil_util.h"
#include "fil_waiter.h"

typedef struct _waiterlist WaiterList;

typedef struct _waiterlist
{
    PyFilWaiter *waiter;
    WaiterList *prev;
    WaiterList *next;
} WaiterList;

typedef struct _pyfil_lock {
    PyObject_HEAD
    int locked;
    WaiterList *waiters;
    WaiterList *last_waiter;
} PyFilLock;

typedef struct _pyfil_rlock {
    PyFilLock lock;
    uint64_t owner;
    uint64_t count;
} PyFilRLock;


static PyFilLock *_lock_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    return (PyFilLock *)type->tp_alloc(type, 0);
}

static int _lock_init(PyFilLock *self, PyObject *args, PyObject *kwargs)
{
    return 0;
}

static void _lock_dealloc(PyFilLock *self)
{
    assert(self->waiters == NULL);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static WaiterList *_waiter_add(PyFilLock *lock, PyFilWaiter *waiter)
{
    WaiterList *waiterlist = malloc(sizeof(WaiterList));

    if (waiterlist == NULL)
        return NULL;
    waiterlist->waiter = waiter;
    waiterlist->next = NULL;
    if ((waiterlist->prev = lock->last_waiter) == NULL)
        lock->waiters = waiterlist;
    else
        waiterlist->prev->next = waiterlist;
    lock->last_waiter = waiterlist;
    return waiterlist;
}

static PyFilWaiter *_waiter_remove(PyFilLock *lock, WaiterList *waiterlist)
{
    PyFilWaiter *waiter = waiterlist->waiter;

    if (waiterlist->prev)
        waiterlist->prev->next = waiterlist->next;
    else
        lock->waiters = waiterlist->next;
    if (waiterlist->next)
        waiterlist->next->prev = waiterlist->prev;
    else
        lock->last_waiter = waiterlist->prev;
    free(waiterlist);
    return waiter;
}

static int __lock_acquire(PyFilLock *lock, int blocking, struct timespec *ts)
{
    WaiterList *waiterlist;
    PyFilWaiter *waiter;

    if (!lock->locked && !lock->waiters)
    {
        lock->locked = 1;
        return 0;
    }

    if (!blocking)
    {
        return 1;
    }

    waiter = fil_waiter_alloc();
    if (waiter == NULL)
    {
        return -1;
    }

    waiterlist = _waiter_add(lock, waiter);
    if (waiterlist == NULL)
    {
        Py_DECREF(waiter);
        PyErr_NoMemory();
        return -1;
    }

    int err = fil_waiter_wait(waiter, ts);
    if (err)
    {
        _waiter_remove(lock, waiterlist);
        Py_DECREF(waiter);
        return -1;
    }

    assert(lock->locked == 1);

    /* signal cleaned waiterlist up */
    Py_DECREF(waiter);

    return 0;
}

static int __lock_release(PyFilLock *lock)
{
    WaiterList *waiterlist;
    PyFilWaiter *waiter;

    if (!lock->locked)
    {
        PyErr_SetString(PyExc_RuntimeError, "release without acquire");
        return -1;
    }

    if ((waiterlist = lock->waiters) == NULL)
    {
        lock->locked = 0;
        return 0;
    }

    /* leave 'locked' set because a different thread is just
     * going to grab it anyway.  This prevents some races without
     * additional work to resolve them.
     */
    waiter = _waiter_remove(lock, waiterlist);
    fil_waiter_signal(waiter, 0);
    return 0;
}

static int __rlock_acquire(PyFilRLock *lock, int blocking, struct timespec *ts)
{
    WaiterList *waiterlist;
    PyFilWaiter *waiter;
    uint64_t owner;

    owner = fil_get_ident();
    if (!lock->lock.locked && !lock->lock.waiters)
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

    waiter = fil_waiter_alloc();
    if (waiter == NULL)
    {
        return -1;
    }

    waiterlist = _waiter_add(&(lock->lock), waiter);
    if (waiterlist == NULL)
    {
        Py_DECREF(waiter);
        PyErr_NoMemory();
        return -1;
    }

    int err = fil_waiter_wait(waiter, ts);
    if (err)
    {
        _waiter_remove(&(lock->lock), waiterlist);
        Py_DECREF(waiter);
        return -1;
    }

    assert(lock->lock.locked == 1);
    lock->owner = fil_get_ident();
    lock->count = 1;

    /* signal cleaned waiterlist up */
    Py_DECREF(waiter);

    return 0;
}

static int __rlock_release(PyFilRLock *lock)
{
    WaiterList *waiterlist;
    PyFilWaiter *waiter;
    uint64_t owner;

    if (!lock->lock.locked)
    {
        PyErr_SetString(PyExc_RuntimeError, "release without acquire");
        return -1;
    }

    owner = fil_get_ident();
    if (owner != lock->owner)
    {
        PyErr_SetString(PyExc_RuntimeError, "not lock owner");
        return -1;
    }

    if (--lock->count > 0)
    {
        return 0;
    }

    lock->owner = 0;

    if ((waiterlist = lock->lock.waiters) == NULL)
    {
        lock->lock.locked = 0;
        return 0;
    }

    /* leave 'locked' set because a different thread is just
     * going to grab it anyway.  This prevents some races without
     * additional work to resolve them.
     */
    waiter = _waiter_remove(&(lock->lock), waiterlist);
    fil_waiter_signal(waiter, 0);
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

    if (fil_timeoutobj_to_timespec(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    blocking = (blockingobj == NULL || blockingobj == Py_True);
    err = __lock_acquire(self, blocking, ts);
    if (err)
        return NULL;
    Py_RETURN_NONE;
    return NULL;
}

PyDoc_STRVAR(_lock_release_doc, "Release the lock.");
static PyObject *_lock_release(PyFilLock *self, PyObject *args)
{
    if (__lock_release(self) < 0)
    {
        return NULL;
    }
    Py_RETURN_NONE;
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

    if (fil_timeoutobj_to_timespec(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    blocking = (blockingobj == NULL || blockingobj == Py_True);
    err = __rlock_acquire(self, blocking, ts);
    if (err)
        return NULL;
    Py_RETURN_NONE;
}

PyDoc_STRVAR(_rlock_release_doc, "Release the lock.");
static PyObject *_rlock_release(PyFilRLock *self, PyObject *args)
{
    if (__rlock_release(self) < 0)
    {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef _lock_methods[] = {
    {"acquire", (PyCFunction)_lock_acquire, METH_VARARGS|METH_KEYWORDS, _lock_acquire_doc},
    {"release", (PyCFunction)_lock_release, METH_NOARGS, _lock_release_doc},
    {"__enter__", (PyCFunction)_lock_acquire, METH_VARARGS|METH_KEYWORDS, _lock_acquire_doc},
    {"__exit__", (PyCFunction)_lock_release, METH_VARARGS, _lock_release_doc},
    { NULL, NULL }
};

static PyMethodDef _rlock_methods[] = {
    {"acquire", (PyCFunction)_rlock_acquire, METH_VARARGS|METH_KEYWORDS, _rlock_acquire_doc},
    {"release", (PyCFunction)_rlock_release, METH_NOARGS, _rlock_release_doc},
    {"__enter__", (PyCFunction)_rlock_acquire, METH_VARARGS, _rlock_acquire_doc},
    {"__exit__", (PyCFunction)_rlock_release, METH_VARARGS, _rlock_release_doc},
    { NULL, NULL }
};

static PyTypeObject _lock_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "filament.lock.Lock",                     /* tp_name */
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
    Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE,     /* tp_flags */
    0,                                          /* tp_doc */
    0,                                          /* tp_traverse */
    0,                                          /* tp_clear */
    0,                                          /* tp_richcompare */
    0,                                          /* tp_weaklistoffset */
    0,                                          /* tp_iter */
    0,                                          /* tp_iternext */
    _lock_methods,                              /* tp_methods */
    0,
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
};

/* Re-entrant lock.  We can use the same calls here */
static PyTypeObject _rlock_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "filament.lock.RLock",                      /* tp_name */
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
    Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE,     /* tp_flags */
    0,                                          /* tp_doc */
    0,                                          /* tp_traverse */
    0,                                          /* tp_clear */
    0,                                          /* tp_richcompare */
    0,                                          /* tp_weaklistoffset */
    0,                                          /* tp_iter */
    0,                                          /* tp_iternext */
    _rlock_methods,                             /* tp_methods */
    0,
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
};


/****************/

int fil_lock_type_init(PyObject *module)
{
    PyObject *m;

    PyGreenlet_Import();
    if (PyType_Ready(&_lock_type) < 0)
        return -1;
    if (PyType_Ready(&_rlock_type) < 0)
        return -1;

    m = fil_create_module("filament.lock");
    if (m == NULL)
        return -1;

    Py_INCREF((PyObject *)&_lock_type);
    Py_INCREF((PyObject *)&_rlock_type);

    if (PyModule_AddObject(m, "Lock", (PyObject *)&_lock_type) != 0)
    {
        Py_DECREF((PyObject *)&_lock_type);
        Py_DECREF((PyObject *)&_rlock_type);
        return -1;
    }

    if (PyModule_AddObject(m, "RLock", (PyObject *)&_rlock_type) != 0)
    {
        /* FIXME: remove LTLock from module */
        Py_DECREF((PyObject *)&_rlock_type);
        return -1;
    }

    if (PyModule_AddObject(module, "lock", m) != 0)
    {
        return -1;
    }

    Py_INCREF(m);

    return 0;
}

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
