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
#include "locking/fil_cond.h"
#include "locking/fil_lock.h"

/*
 * Mutual exclusion for the condition's waiter list.
 *
 * None on a stock build.  On a FREE-THREADING build, wait() has the classic
 * condition-variable hole: it releases the caller's lock and only then adds
 * itself to the waiter list, so a notify() in that window signals an empty list
 * and the waiter sleeps through its own notification.  The GIL closed that
 * window by making the release-and-add indivisible; without it we have to.
 *
 * The waiter therefore joins the list BEFORE lock.release() is called (safe:
 * an early notify() just sets SIGNALED, which fil_waiter_wait() tests before
 * parking), and the mutex is held only around the list operations themselves
 * -- never across a PyObject_CallMethod, whose target is an arbitrary user
 * lock that could re-enter this Condition and self-deadlock.  Order is
 * cond_lock -> waiter_lock -> sched_lock and nothing runs it backwards.
 */
#ifdef Py_GIL_DISABLED
#  define FIL_COND_LOCK(__c)    pthread_mutex_lock(&((__c)->mutex))
#  define FIL_COND_UNLOCK(__c)  pthread_mutex_unlock(&((__c)->mutex))
#  define FIL_COND_WAIT(__c, __list, __ts, __exc) \
       fil_waiterlist_wait_locked(__list, __ts, __exc, &((__c)->mutex))
#else
#  define FIL_COND_LOCK(__c)    ((void)0)
#  define FIL_COND_UNLOCK(__c)  ((void)0)
#  define FIL_COND_WAIT(__c, __list, __ts, __exc) \
       fil_waiterlist_wait(__list, __ts, __exc)
#endif

typedef struct _pyfil_cond {
    PyObject_HEAD
    PyObject *lock;
#ifdef Py_GIL_DISABLED
    pthread_mutex_t mutex;
#endif
    PyObject *verbose;
    FilWaiterList waiters;
} PyFilCond;


static PyFilCond *_cond_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    PyFilCond *self = (PyFilCond *)type->tp_alloc(type, 0);

    if (self != NULL)
    {
        fil_waiterlist_init(self->waiters);
#ifdef Py_GIL_DISABLED
        pthread_mutex_init(&(self->mutex), NULL);
#endif
    }

    return self;
}

static int _cond_init(PyFilCond *self, PyObject *args, PyObject *kwargs)
{
    PyObject *lock = NULL;
    PyObject *verbose = NULL;

    static char *keywords[] = {"lock", "verbose", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO",
                                     keywords,
                                     &lock, &verbose))
    {
        return -1;
    }

    if (lock == NULL)
    {
        lock = (PyObject *)fil_rlock_alloc();
        if (lock == NULL)
        {
            return -1;
        }
    }
    else
    {
        Py_INCREF(lock);
    }

    Py_XINCREF(verbose);

    Py_XSETREF(self->lock, lock);
    Py_XSETREF(self->verbose, verbose);

    return 0;
}

/*
 * GC support.  'lock' is an arbitrary user object that can (and in the
 * bound-method case routinely does) refer back to this Condition; without a
 * traverse that cycle is invisible to the collector and leaks.  The waiter
 * list needs no traversal: waiters are plain C structs that only exist while
 * a greenthread is parked, and a parked greenthread is not collectable.
 */
static int _cond_traverse(PyFilCond *self, visitproc visit, void *arg)
{
    Py_VISIT(self->lock);
    Py_VISIT(self->verbose);
    return 0;
}

static int _cond_clear(PyFilCond *self)
{
    Py_CLEAR(self->lock);
    Py_CLEAR(self->verbose);
    return 0;
}

static void _cond_dealloc(PyFilCond *self)
{
    PyObject_GC_UnTrack(self);
    assert(fil_waiterlist_empty(self->waiters));
#ifdef Py_GIL_DISABLED
    pthread_mutex_destroy(&(self->mutex));
#endif
    Py_CLEAR(self->lock);
    Py_CLEAR(self->verbose);

    /* Respect tp_free: Python subclass instances are GC-allocated, and
     * PyObject_Del on them frees the wrong pointer (heap corruption). */
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static int __cond_wait(PyFilCond *cond, struct timespec *ts)
{
    PyObject *result;
    FilWaiter *waiter;
    FilWaiterList *entry;
    int err;

    waiter = fil_waiter_alloc();
    if (waiter == NULL)
    {
        return -1;
    }
    entry = &(waiter->waiter_list);

    /* Join the waiter list BEFORE calling the lock's release(), holding the
     * condition's own mutex (a no-op on stock builds) only around the list
     * itself.  Two problems die at once:
     *
     *  - the classic condition-variable hole: release-then-add leaves a
     *    window where a notify() sweeps an empty list and the waiter sleeps
     *    through its own notification.  The GIL only ever closed it for
     *    locks whose release() cannot switch greenthreads; a Python-level
     *    lock that parks inside release() reopened it even on stock builds.
     *
     *  - cond->lock is an arbitrary object, so holding a non-recursive
     *    pthread mutex across PyObject_CallMethod meant a release() -- or a
     *    GC finalizer it triggers -- touching this same Condition would
     *    self-deadlock on a free-threading build.
     *
     * Joining this early is safe: a notify() that arrives before we park
     * just sets SIGNALED, and fil_waiter_wait() tests SIGNALED before doing
     * anything else. */
    FIL_COND_LOCK(cond);
    _fil_waiterlist_add(&(cond->waiters), waiter);
    FIL_COND_UNLOCK(cond);

    result = PyObject_CallMethod(cond->lock, "release", NULL);
    Py_XDECREF(result);
    if (result == NULL)
    {
        /* Never parked.  Take ourselves back off the list -- unless a
         * notify() already popped us (the detached entry self-points; see
         * __fil_waiterlist_signal_first), in which case its notification
         * must be passed on rather than dying with us. */
        FIL_COND_LOCK(cond);
        if (entry->next == entry)
        {
            _fil_waiterlist_signal_first_keep_exc(&(cond->waiters));
        }
        else
        {
            _fil_waiterlist_del(entry);
        }
        FIL_COND_UNLOCK(cond);
        fil_waiter_decref(waiter);
        return -1;
    }

    err = fil_waiter_wait(waiter, ts, NULL);

    FIL_COND_LOCK(cond);
    if (err)
    {
        if (entry->next == entry)
        {
            /* A notify() popped us after we gave up -- same race as in
             * _fil_waiterlist_wait_locked.  Its notification is ours: a
             * plain timeout loses to it (report success), a throw carries
             * it out through the unwind path below. */
            if (err == -ETIMEDOUT)
            {
                PyErr_Clear();
                err = 0;
            }
            else if (err < 0)
            {
                err = FIL_WAITER_SIGNALED_UNWIND;
            }
        }
        else
        {
            _fil_waiterlist_del(entry);
        }
    }
    FIL_COND_UNLOCK(cond);
    fil_waiter_decref(waiter);

    if (err)
    {
        PyObject *exc_type, *exc_value, *exc_tb;
        PyErr_Fetch(&exc_type, &exc_value, &exc_tb);

        if (err == FIL_WAITER_SIGNALED_UNWIND)
        {
            /* notify() woke us and something threw into us in the same
             * wakeup.  We cannot use the notification, so pass it to the next
             * waiter rather than swallowing it -- a notify_all() that reaches
             * a greenthread being killed must still reach the others. */
            FIL_COND_LOCK(cond);
            fil_waiterlist_signal_first(cond->waiters);
            FIL_COND_UNLOCK(cond);
        }

        result = PyObject_CallMethod(cond->lock, "acquire", NULL);
        Py_XDECREF(result);
        PyErr_Restore(exc_type, exc_value, exc_tb);
        return -1;
    }

    result = PyObject_CallMethod(cond->lock, "acquire", NULL);
    Py_XDECREF(result);

    return (result == NULL) ? -1 : 0;
}

static int __cond_notify(PyFilCond *cond, int num)
{
    int count = 0;

    FIL_COND_LOCK(cond);
    for(;(num < 0) || (count < num);count++)
    {
        if (fil_waiterlist_empty(cond->waiters))
        {
            FIL_COND_UNLOCK(cond);
            return 0;
        }
        fil_waiterlist_signal_first(cond->waiters);
    }
    FIL_COND_UNLOCK(cond);

    return 0;
}

PyDoc_STRVAR(_cond_wait_doc, "Wait for a condition.");
static PyObject *_cond_wait(PyFilCond *self, PyObject *args, PyObject *kwargs)
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

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    err = __cond_wait(self, ts);
    if (err)
        return NULL;
    Py_RETURN_NONE;
}

PyDoc_STRVAR(_cond_notify_doc, "Notify condition waiter(s).");
static PyObject *_cond_notify(PyFilCond *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"n", NULL};
    int n = 1;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|i",
                                     keywords,
                                     &n))
    {
        return NULL;
    }

    if (__cond_notify(self, n) < 0)
    {
        return NULL;
    }
    Py_RETURN_NONE;
}

PyDoc_STRVAR(_cond_notifyall_doc, "Notify all condition waiters.");
static PyObject *_cond_notifyall(PyFilCond *self, PyObject *args)
{
    if (__cond_notify(self, -1) < 0)
    {
        return NULL;
    }
    Py_RETURN_NONE;
}

PyDoc_STRVAR(_cond_enter_doc, "Lock the underlying lock.");
static PyObject *_cond_enter(PyFilCond *self)
{
    PyObject *res = PyObject_CallMethod(self->lock, "acquire", NULL);
    if (res == NULL)
    {
        return NULL;
    }
    Py_DECREF(res);
    Py_INCREF(self);
    return (PyObject *)self;
}

PyDoc_STRVAR(_cond_exit_doc, "Release the underlying lock.");
static PyObject *_cond_exit(PyFilCond *self, PyObject *args)
{
    return PyObject_CallMethod(self->lock, "release", NULL);
}

static PyMethodDef _cond_methods[] = {
    {"wait", (PyCFunction)_cond_wait, METH_VARARGS|METH_KEYWORDS, _cond_wait_doc},
    {"notify", (PyCFunction)_cond_notify, METH_VARARGS|METH_KEYWORDS, _cond_notify_doc},
    {"notifyAll", (PyCFunction)_cond_notifyall, METH_NOARGS, _cond_notifyall_doc},
    {"notify_all", (PyCFunction)_cond_notifyall, METH_NOARGS, _cond_notifyall_doc},
    {"__enter__", (PyCFunction)_cond_enter, METH_NOARGS, _cond_enter_doc},
    {"__exit__", (PyCFunction)_cond_exit, METH_VARARGS, _cond_exit_doc},
    { NULL, NULL }
};

static PyTypeObject _cond_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "_filament.locking.Condition",              /* tp_name */
    sizeof(PyFilCond),                          /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_cond_dealloc,                  /* tp_dealloc */
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
    FIL_DEFAULT_TPFLAGS|Py_TPFLAGS_HAVE_GC,     /* tp_flags */
    0,                                          /* tp_doc */
    (traverseproc)_cond_traverse,                 /* tp_traverse */
    (inquiry)_cond_clear,                         /* tp_clear */
    0,                                          /* tp_richcompare */
    0,                                          /* tp_weaklistoffset */
    0,                                          /* tp_iter */
    0,                                          /* tp_iternext */
    _cond_methods,                              /* tp_methods */
    0,                                          /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_cond_init,                       /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_cond_new,                         /* tp_new */
    /* Must match tp_alloc: PyType_GenericAlloc allocates (and tracks) a GC
     * header for Py_TPFLAGS_HAVE_GC types, so freeing with PyObject_Del
     * corrupts the heap. */
    PyObject_GC_Del,                            /* tp_free */
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

int fil_cond_type_init(PyObject *module)
{
    PyFilCore_Import();

    if (PyType_Ready(&_cond_type) < 0)
    {
        return -1;
    }

    Py_INCREF((PyObject *)&_cond_type);
    if (PyModule_AddObject(module, "Condition",
                           (PyObject *)&_cond_type) != 0)
    {
        Py_DECREF((PyObject *)&_cond_type);
        return -1;
    }

    return 0;
}
