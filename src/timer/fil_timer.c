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

#define __FIL_BUILDING_TIMER__
#include "core/filament.h"
#include "timer/fil_timer.h"

typedef struct _pyfil_timer {
    PyObject_HEAD
#define FIL_TIMER_FLAGS_CANCELLED  0x00000001
    uint32_t flags;
    PyObject *func;
    PyObject *args;
    PyObject *kwargs;
    /* The scheduler we armed on (strong ref, so it cannot go away under a
     * pending timer), and our handle on the queued event.  The scheduler
     * NULLs 'event' the moment it takes the event out of the queue, so a
     * non-NULL 'event' under sched_lock is exactly the condition for cancel()
     * being able to unlink it. */
    PyFilScheduler *sched;
    FilSchedEvent *event;
#ifdef Py_GIL_DISABLED
    /* Guards {flags, func, args, kwargs, sched, event} as a unit.  On a
     * stock build the GIL serializes init/cancel/callback; without it two
     * cancel()s can both win the del_event test and the loser then reads
     * 'sched' after the winner dropped the last reference to it.  All
     * Py_DECREFs happen OUTSIDE this lock (a destructor can re-enter this
     * very timer), and PyObject_Call runs outside it too.  Ordering is
     * timer_lock -> sched_lock; the scheduler never calls back in while
     * holding sched_lock (event callbacks run with it dropped). */
    pthread_mutex_t lock;
#endif
} PyFilTimer;

#ifdef Py_GIL_DISABLED
#  define FIL_TIMER_INIT(__t)    pthread_mutex_init(&((__t)->lock), NULL)
#  define FIL_TIMER_DESTROY(__t) pthread_mutex_destroy(&((__t)->lock))
#  define FIL_TIMER_LOCK(__t)    pthread_mutex_lock(&((__t)->lock))
#  define FIL_TIMER_UNLOCK(__t)  pthread_mutex_unlock(&((__t)->lock))
#else
#  define FIL_TIMER_INIT(__t)    ((void)0)
#  define FIL_TIMER_DESTROY(__t) ((void)0)
#  define FIL_TIMER_LOCK(__t)    ((void)0)
#  define FIL_TIMER_UNLOCK(__t)  ((void)0)
#endif

typedef struct _pyfil_localtimer {
    PyFilTimer timer;
    PyGreenlet *src_gl;
} PyFilLocalTimer;


static void _timer_callback(PyFilScheduler *sched, PyFilTimer *timer)
{
    PyObject *func = NULL, *args = NULL, *kwargs = NULL;

    /* Snapshot-and-clear under the lock, call outside it: the user callback
     * (or any destructor these decrefs run) may cancel() this same timer,
     * and the lock is not recursive. */
    FIL_TIMER_LOCK(timer);
    if (!(timer->flags & FIL_TIMER_FLAGS_CANCELLED))
    {
        func = timer->func;
        timer->func = NULL;
        args = timer->args;
        timer->args = NULL;
        kwargs = timer->kwargs;
        timer->kwargs = NULL;
    }
    FIL_TIMER_UNLOCK(timer);

    if (func != NULL)
    {
        PyObject *result;

        result = PyObject_Call(func, args, kwargs);
        Py_XDECREF(result);
        Py_DECREF(func);
        Py_XDECREF(args);
        Py_XDECREF(kwargs);
    }
    Py_DECREF(timer);
}

static PyFilTimer *_timer_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    PyFilTimer *self = NULL;

    self = (PyFilTimer *)type->tp_alloc(type, 0);
    if (self == NULL)
        return NULL;
    FIL_TIMER_INIT(self);
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

    /* A zero delay (or None) means "fire on the next scheduler pass".  Use
     * an immediate (ts == NULL) event rather than a now-stamped one:
     * immediate events are FIFO with other immediate wakeups (sleep(0) &
     * friends), so 'schedule the callback, then yield' idioms keep their
     * relative order, and the scheduler skips the timestamp bookkeeping
     * entirely.  fil_double_from_timeout_obj() maps None to -1.0 and raises
     * on any other negative value, so <= 0.0 here is exactly {None, 0}. */
    {
        double timeout_dbl;

        if ((err = fil_double_from_timeout_obj(timeout, &timeout_dbl)) < 0)
        {
            return -1;
        }
        if (timeout_dbl <= 0.0)
        {
            ts = NULL;
        }
        else if ((err = _fil_ts_from_double(timeout_dbl, &tsbuf, &ts)) < 0)
        {
            return -1;
        }
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
        Py_DECREF(sched);
        PyErr_SetString(PyExc_TypeError, "Timer() 2nd argument should be a callable");
        return -1;
    }

    method_args = PyTuple_GetSlice(args, 2, args_len);
    if (method_args == NULL)
    {
        Py_DECREF(sched);
        return -1;
    }

    /* Publish the fields and arm the event as one unit: a concurrent
     * __init__ on a free-threading build must see either "not initialized"
     * or the fully-armed timer, never the half-written middle.  add_event
     * takes sched_lock inside, matching cancel()'s ordering. */
    FIL_TIMER_LOCK(self);

    if (self->func != NULL)
    {
        FIL_TIMER_UNLOCK(self);
        Py_DECREF(sched);
        Py_DECREF(method_args);
        PyErr_SetString(PyExc_TypeError, "Timer() already initialized");
        return -1;
    }

    Py_INCREF(method);
    self->func = method;
    self->args = method_args;
    Py_XINCREF(kwargs);
    self->kwargs = kwargs;

    Py_INCREF(self);

    /* Keep the scheduler reference: cancel() needs it to unlink the event,
     * and it must not be torn down while our event is still queued. */
    self->sched = sched;

    err = fil_scheduler_add_event_ref(sched, ts, 0,
                                      (fil_event_cb_t)_timer_callback, self,
                                      &self->event);
    if (err)
    {
        /* Unpublish under the lock, decref outside it: a destructor could
         * re-enter this timer. */
        self->sched = NULL;
        self->func = NULL;
        self->args = NULL;
        self->kwargs = NULL;
        FIL_TIMER_UNLOCK(self);
        Py_DECREF(sched);
        Py_DECREF(method);
        Py_DECREF(method_args);
        Py_XDECREF(kwargs);
        Py_DECREF(self);
        return -1;
    }

    FIL_TIMER_UNLOCK(self);
    return 0;
}

static void _timer_dealloc(PyFilTimer *self)
{
    /* A queued event holds a reference to us, so by the time we get here the
     * event has either fired or been cancelled -- self->event is NULL. */
    Py_CLEAR(self->sched);
    Py_CLEAR(self->func);
    Py_CLEAR(self->args);
    Py_CLEAR(self->kwargs);

    FIL_TIMER_DESTROY(self);

    /* Respect tp_free: Python subclass instances are GC-allocated, and
     * PyObject_Del on them frees the wrong pointer (heap corruption). */
    Py_TYPE(self)->tp_free((PyObject *)self);
}

PyDoc_STRVAR(_timer_cancel_doc, "Cancel the timer.");
static PyObject *_timer_cancel(PyFilTimer *self, PyObject *args)
{
    PyObject *func = NULL, *targs = NULL, *kwargs = NULL;
    PyFilScheduler *sched = NULL;
    int won = 0;

    FIL_TIMER_LOCK(self);

    /* The flag alone still matters: the event may already be out of the queue
     * and on its way to running, in which case unlinking is impossible and
     * this is what stops the callback. */
    self->flags |= FIL_TIMER_FLAGS_CANCELLED;

    /* Drop the event out of the scheduler rather than leaving a dead entry
     * behind until its deadline.  A cancelled 60s timeout that lingers costs
     * a node and a reference for the full minute -- with a timeout armed per
     * request (which is what any HTTP client does) that is unbounded growth
     * of both memory and the timer heap.
     *
     * Everything is read and unpublished under the timer lock (two racing
     * cancel()s must not both see 'sched', or the loser dereferences it
     * after the winner dropped the last reference), but the decrefs happen
     * outside it -- a destructor can re-enter this timer. */
    if (self->event != NULL && self->sched != NULL &&
        fil_scheduler_del_event(self->sched, &self->event))
    {
        /* We took the event, so we own what its callback would have
         * released. */
        won = 1;
        func = self->func;
        self->func = NULL;
        targs = self->args;
        self->args = NULL;
        kwargs = self->kwargs;
        self->kwargs = NULL;
        sched = self->sched;
        self->sched = NULL;
    }

    FIL_TIMER_UNLOCK(self);

    if (won)
    {
        Py_XDECREF(func);
        Py_XDECREF(targs);
        Py_XDECREF(kwargs);
        Py_XDECREF(sched);
        Py_DECREF(self);
    }

    Py_RETURN_NONE;
}

static PyMethodDef _timer_methods[] = {
    {"cancel", (PyCFunction)_timer_cancel, METH_VARARGS|METH_KEYWORDS, _timer_cancel_doc},
    { NULL, NULL }
};

static PyTypeObject _timer_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "_filament.timer.Timer",                    /* tp_name */
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
    FIL_DEFAULT_TPFLAGS,                        /* tp_flags */
    0,                                          /* tp_doc */
    0,                                          /* tp_traverse */
    0,                                          /* tp_clear */
    0,                                          /* tp_richcompare */
    0,                                          /* tp_weaklistoffset */
    0,                                          /* tp_iter */
    0,                                          /* tp_iternext */
    _timer_methods,                             /* tp_methods */
    0,                                          /* tp_members */
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
    0,                                          /* tp_is_gc */
    0,                                          /* tp_bases */
    0,                                          /* tp_mro */
    0,                                          /* tp_cache */
    0,                                          /* tp_subclasses */
    0,                                          /* tp_weaklist */
    0,                                          /* tp_del */
    0,                                          /* tp_version_tag */
};

PyDoc_STRVAR(_fil_timer_module_doc, "Filament _filament.timer module.");
static PyMethodDef _fil_timer_module_methods[] = {
    { NULL, },
};

_FIL_MODULE_INIT_FN_NAME(timer)
{
    PyObject *m;

    PyFilCore_Import();

    _FIL_MODULE_SET(m, "_filament.timer", _fil_timer_module_methods, _fil_timer_module_doc);
    if (m == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    if (PyType_Ready(&_timer_type) < 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    Py_INCREF((PyObject *)&_timer_type);
    if (PyModule_AddObject(m, "Timer", (PyObject *)&_timer_type) != 0)
    {
        Py_DECREF((PyObject *)&_timer_type);
        return _FIL_MODULE_INIT_ERROR;
    }

    return _FIL_MODULE_INIT_SUCCESS(m);
}
