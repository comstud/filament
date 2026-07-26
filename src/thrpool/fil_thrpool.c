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

#define __FIL_BUILDING_THRPOOL__
#include "core/filament.h"

static PyObject *_EMPTY_TUPLE;

#define FIL_THRPOOL_DEFAULT_TIMEOUT 10.0

typedef struct _pyfil_thr_pool {
    PyObject_HEAD
    char is_shutdown;
    char in_registry;
    FilThrPool *tpool;
    struct _pyfil_thr_pool *registry_prev;
    struct _pyfil_thr_pool *registry_next;
} PyFilThrPool;

/*
 * Registry of every live, not-yet-shut-down pool.
 *
 * Why it exists: a pool that is still alive when the interpreter starts
 * finalizing can never be shut down, because shutting a pool down spawns a
 * helper thread that immediately calls PyGILState_Ensure() -- and during
 * finalization CPython does not hand the GIL back to a non-finalizing thread,
 * it simply makes that thread exit.  The helper therefore dies before it can
 * flag the pool as shutting down or signal the waiter, and whoever asked for
 * the shutdown blocks forever.  So we shut the pools down *before*
 * finalization begins, from an atexit callback, while the runtime is still
 * fully alive and worker threads can still attach a thread state and be
 * joined.  (Same reasoning as filament/thrpool_resolver.py's atexit hook,
 * except this one also covers pools created directly from C/Python without
 * going through any of the filament wrappers.)
 *
 * REFERENCE SEMANTICS: the registry holds BORROWED references.  Strong ones
 * would make every pool immortal -- tp_dealloc would never run, so a pool that
 * the program legitimately dropped would keep four idle OS threads alive for
 * the life of the process, and _thrpool_dealloc's implicit shutdown would
 * never happen.  Borrowing is safe because every mutation below happens with
 * the GIL held (_thrpool_new / _thrpool_shutdown_async / _thrpool_dealloc) and
 * because _thrpool_dealloc unlinks the pool as its very first act, so the
 * registry can never contain a dead pointer.  The one place that has to be
 * careful is the atexit sweep, which blocks (and therefore lets other
 * greenthreads run and other pools die) while holding a pool pointer; it
 * takes a real reference for the duration -- see _thrpool_atexit().
 */
static PyFilThrPool *_thrpool_registry;

static void _thrpool_registry_add(PyFilThrPool *self)
{
    if (self->in_registry)
    {
        return;
    }
    self->registry_prev = NULL;
    self->registry_next = _thrpool_registry;
    if (_thrpool_registry != NULL)
    {
        _thrpool_registry->registry_prev = self;
    }
    _thrpool_registry = self;
    self->in_registry = 1;
}

/* Idempotent: safe to call on a pool that was never added or already removed. */
static void _thrpool_registry_remove(PyFilThrPool *self)
{
    if (!self->in_registry)
    {
        return;
    }
    if (self->registry_prev != NULL)
    {
        self->registry_prev->registry_next = self->registry_next;
    }
    else
    {
        _thrpool_registry = self->registry_next;
    }
    if (self->registry_next != NULL)
    {
        self->registry_next->registry_prev = self->registry_prev;
    }
    self->registry_prev = NULL;
    self->registry_next = NULL;
    self->in_registry = 0;
}

typedef struct _pyfil_thrinit_info
{
    PyFilThrPool *tpool;
    PyGILState_STATE gstate;
    PyThreadState *thr_state;
} PyFilThrState;

static PyFilThrState *_thrpool_initthr_cb(PyFilThrPool *tpool)
{
    PyFilThrState *thr_state;

    thr_state = malloc(sizeof(*thr_state));
    if (thr_state == NULL)
    {
        return FIL_THRPOOL_THR_INIT_FAILURE_RESULT;
    }
    thr_state->tpool = tpool;
    thr_state->gstate = PyGILState_Ensure();
    thr_state->thr_state = PyEval_SaveThread();
    return thr_state;
}

static void _thrpool_deinitthr_cb(PyFilThrState *thr_state)
{
    if (thr_state != FIL_THRPOOL_THR_INIT_FAILURE_RESULT)
    {
        if (fil_py_is_finalizing())
        {
            /* The interpreter is tearing down; attaching this worker's
             * thread state now would abort (the gilstate TSS key is gone on
             * 3.14+).  Leak the thread state -- the process is exiting. */
            free(thr_state);
            return;
        }
        PyEval_RestoreThread(thr_state->thr_state);
        PyGILState_Release(thr_state->gstate);
        free(thr_state);
    }
}

static PyFilThrPool *_thrpool_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    PyFilThrPool *self = NULL;

    self = (PyFilThrPool *)type->tp_alloc(type, 0);
    if (self != NULL)
    {
        static char *keywords[] = {"min_threads", "max_threads", "stack_size", NULL};
        int min_threads = FIL_THRPOOL_DEFAULT_MIN_THREADS, max_threads = FIL_THRPOOL_DEFAULT_MAX_THREADS;
        int stack_size = FIL_THRPOOL_DEFAULT_STACK_SIZE;
        FilThrPoolOpt tpool_opt;
        FilThrPool *tpool;

        if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|iii:ThreadPool", keywords,
                    &min_threads, &max_threads, &stack_size))
        {
            Py_DECREF(self);
            return NULL;
        }

        if (min_threads < 0)
        {
            Py_DECREF(self);
            PyErr_SetString(PyExc_ValueError, "min_threads must be >= 0");
            return NULL;
        }

        if (max_threads <= 0)
        {
            Py_DECREF(self);
            PyErr_SetString(PyExc_ValueError, "max_threads must be > 0");
            return NULL;
        }

        if (stack_size < (64 * 1024))
        {
            Py_DECREF(self);
            PyErr_SetString(PyExc_ValueError, "stack_size must be >= 64K");
            return NULL;
        }

        fil_thrpool_opt_init(&tpool_opt);
        tpool_opt.min_thr = (uint32_t)min_threads;
        tpool_opt.max_thr = (uint32_t)max_threads;
        tpool_opt.stack_size = (uint32_t)stack_size;
        tpool_opt.thr_init_cb = (FilThrPoolInitThrCallback)_thrpool_initthr_cb;
        tpool_opt.thr_init_cb_arg = self;
        tpool_opt.thr_deinit_cb = (FilThrPoolDeinitThrCallback)_thrpool_deinitthr_cb;

        tpool = fil_thrpool_create(&tpool_opt);
        if (tpool == NULL)
        {
            if (errno == ENOMEM)
            {
                PyErr_SetString(PyExc_MemoryError, "out of memory");
            }
            else
            {
                PyErr_Format(PyExc_RuntimeError, "Error creating thread pool: %d", errno);
            }
            Py_DECREF(self);
            return NULL;
        }

        self->tpool = tpool;

        /* GIL held: no extra locking needed. */
        _thrpool_registry_add(self);
    }
    return self;
}

static int _thrpool_init(PyFilThrPool *self, PyObject *args, PyObject *kwargs)
{
    return 0;
}

typedef struct _thrpool_shutdown_info
{
    PyFilThrPool *self;
    FilWaiter *waiter;
    int do_free;
} PyFilThrPoolShutdownInfo;

static void _thrpool_shutdown_finish(PyFilThrState *thr_state, PyFilThrPoolShutdownInfo *info)
{
    PyEval_RestoreThread(thr_state->thr_state);

    if (info->waiter)
    {
        fil_waiter_signal(info->waiter);
    }

    info->self->tpool = NULL;
    if (info->do_free)
    {
        Py_TYPE(info->self)->tp_free((PyObject *)info->self);
    }
    else
    {
        Py_DECREF(info->self);
    }

    thr_state->thr_state = PyEval_SaveThread();
    free(info);

    return;
}

static int _thrpool_shutdown_async(PyFilThrPool *self, int now, int wait, int do_free)
{
    PyFilThrPoolShutdownInfo *info;
    FilWaiter *waiter = NULL;
    int err;

    if (wait)
    {
        if ((waiter = fil_waiter_alloc()) == NULL)
        {
            return -1;
        }
    }

    info = malloc(sizeof(*info));
    if (info == NULL)
    {
        PyErr_SetString(PyExc_MemoryError, "out of memory");
        return -1;
    }

    if (!do_free)
    {
        Py_INCREF(self);
    }
    info->self = self;
    info->waiter = waiter;
    info->do_free = do_free;

    self->is_shutdown = 1;

    if ((err = fil_thrpool_shutdown_async(self->tpool, now, (FilThrPoolShutdownCallback)_thrpool_shutdown_finish, info)) != 0)
    {
        if (err == -ENOMEM)
        {
            PyErr_SetString(PyExc_MemoryError, "out of memory");
        }
        else
        {
            PyErr_Format(PyExc_RuntimeError, "couldn't shut down thread pool: %d", err);
        }
        self->is_shutdown = 0;
        if (waiter != NULL)
        {
            fil_waiter_decref(waiter);
        }
        free(info);
        Py_DECREF(self);
        return err;
    }

    /* The pool is now on its way out, so it no longer needs the atexit sweep.
     * Done only on the success path: the error path above put 'is_shutdown'
     * back and the pool is still a live pool that must be swept at exit. */
    _thrpool_registry_remove(self);

    if (waiter != NULL)
    {
        int err = fil_waiter_wait(waiter, NULL, NULL);

        if (err)
        {
            /* most likely a signal that triggered an exception */
            /* let the background free 'info' */
            info->waiter = NULL;
        }

        fil_waiter_decref(waiter);

        return err;
    }

    return 0;
}

/*
 * FOOT-GUN: if the pool was never explicitly shut down, deallocation triggers
 * a shutdown here. _thrpool_shutdown_async(..., wait=1) BLOCKS until all queued
 * work drains and worker threads join. Because tp_dealloc can run at arbitrary
 * points during garbage collection (whenever the last reference drops), this
 * can stall the interpreter at an unexpected time and, if any pending task
 * needs the GIL/this thread, deadlock. The correct usage is to call
 * shutdown() explicitly before dropping the last reference. We intentionally
 * do NOT change the blocking behaviour here for a live interpreter (callers
 * may rely on tasks completing), but the hazard is documented so it is not
 * mistaken for correct lifecycle management.
 *
 * The one case where the blocking shutdown is not merely rude but *fatal* is
 * interpreter finalization, so that case is special-cased below.  It should
 * normally be unreachable now: the atexit sweep (_thrpool_atexit) shuts every
 * live pool down before finalization starts.  It can still be reached if
 * atexit registration failed, if the process is torn down via a path that
 * skips atexit handlers, or if a pool is created after the handlers have run.
 */
static void _thrpool_dealloc(PyFilThrPool *self)
{
    /* Unlink first, so the registry never holds a pointer to an object that
     * is on its way out -- everything below can block and run other code. */
    _thrpool_registry_remove(self);

    if (self->tpool != NULL && !self->is_shutdown)
    {
        if (fil_py_is_finalizing())
        {
            /*
             * DELIBERATE LEAK.  Shutting down spawns a helper thread whose
             * first act is PyGILState_Ensure(); during finalization CPython
             * never returns from that call on a non-finalizing thread -- it
             * makes the thread exit instead.  The helper would therefore die
             * before flagging the shutdown or signalling the waiter, and this
             * thread would block in fil_waiter_wait() forever (that is the
             * classic "hangs at exit, but only when stdout is a pipe" bug).
             *
             * So do nothing at all: no shutdown, and no tp_free either.  The
             * FilThrPool and its worker threads stay allocated, and 'self'
             * stays allocated too because the pool's thr_init_cb_arg points
             * at it -- freeing it would leave that dangling.  The workers are
             * parked in pthread_cond_wait() inside _fil_thr_pool_thread();
             * nobody will ever signal them, so they never touch the dying
             * interpreter.  The process is exiting and exit() does not wait
             * on threads, so leaking a few pages and some idle threads for
             * the last microseconds of the process is the correct trade
             * against a guaranteed hang.
             */
            return;
        }

        _thrpool_shutdown_async(self, 1, 1, 1);
        return;
    }

    /* Respect tp_free: Python subclass instances are GC-allocated, and
     * PyObject_Del on them frees the wrong pointer (heap corruption). */
    Py_TYPE(self)->tp_free((PyObject *)self);
}

typedef struct _pyfil_thrpool_run_info
{
    FilWaiter *waiter;
    PyObject *method;
    PyObject *args;
    PyObject *kwargs;
    PyObject *res_or_exc_type;
    PyObject *exc_type;
    PyObject *exc_value;
    PyObject *exc_tb;
#define PYFIL_THRPOOL_RUN_INFO_FLAGS_FAILURE    0x00000001
#define PYFIL_THRPOOL_RUN_INFO_FLAGS_CANCEL     0x00000002
#define PYFIL_THRPOOL_RUN_INFO_FLAGS_EXC        0x00000004
#define PYFIL_THRPOOL_RUN_INFO_FLAGS_TIMED      0x00000008
    uint32_t flags;
} PyFilThrPoolRunInfo;

static void _thrpool_run_async(PyFilThrState *thr_state, PyFilThrPoolRunInfo *info, uint32_t flags)
{
    PyGILState_STATE gstate;
    PyGILState_STATE *gstate_ptr = NULL;

    if (thr_state == FIL_THRPOOL_THR_INIT_FAILURE_RESULT)
    {
        /* this can only happen on a shutdown. skip running the callback */
        gstate = PyGILState_Ensure();
        gstate_ptr = &gstate;
        info->flags |= PYFIL_THRPOOL_RUN_INFO_FLAGS_FAILURE;
    }
    else
    {
        PyEval_RestoreThread(thr_state->thr_state);
    }

    if (!(info->flags & (PYFIL_THRPOOL_RUN_INFO_FLAGS_FAILURE|
                        PYFIL_THRPOOL_RUN_INFO_FLAGS_CANCEL)))
    {
        PyObject *kwargs = NULL;
        int need_kwargs = (info->kwargs || flags & FIL_THRPOOL_CALLBACK_FLAGS_SHUTDOWN);

        if (need_kwargs && ((kwargs = PyDict_New()) != NULL))
        {
            if (info->kwargs && PyDict_SetItemString(kwargs, "kwargs", info->kwargs) < 0)
            {
                Py_DECREF(kwargs);
                kwargs = NULL;
            }
            if (kwargs != NULL && (flags & FIL_THRPOOL_CALLBACK_FLAGS_SHUTDOWN))
            {
                if (PyDict_SetItemString(kwargs, "shutdown", Py_True) < 0)
                {
                    Py_DECREF(kwargs);
                    kwargs = NULL;
                }
            }
        }

        if (need_kwargs && kwargs == NULL)
        {
            info->flags |= PYFIL_THRPOOL_RUN_INFO_FLAGS_FAILURE;
        }
        else
        {
            info->res_or_exc_type = PyObject_Call(info->method, info->args, kwargs);
            if (info->res_or_exc_type == NULL)
            {
                PyErr_Fetch(&(info->res_or_exc_type), &(info->exc_value), &(info->exc_tb));
                info->flags |= PYFIL_THRPOOL_RUN_INFO_FLAGS_EXC;
            }
            Py_XDECREF(kwargs);
        }
    }

    Py_CLEAR(info->method);
    Py_CLEAR(info->args);
    Py_CLEAR(info->kwargs);

    if (!(info->flags & PYFIL_THRPOOL_RUN_INFO_FLAGS_CANCEL) && info->waiter != NULL)
    {
        /* NOTE: the CANCEL check above, this signal, and the waiting side's
         * error path (which sets CANCEL and then fil_waiter_decref()s the
         * waiter) are serialized BY THE GIL: we must not release the GIL
         * between the check and the signal, or an exception-resumed waiter
         * could free the waiter out from under us. */
        fil_waiter_signal(info->waiter);
    }
    else
    {
        Py_XDECREF(info->res_or_exc_type);
        Py_XDECREF(info->exc_value);
        Py_XDECREF(info->exc_tb);
        free(info);
    }

    if (gstate_ptr)
    {
        PyGILState_Release(*gstate_ptr);
    }
    else
    {
        thr_state->thr_state = PyEval_SaveThread();
    }
}

PyDoc_STRVAR(_thrpool_run_doc,
"Run a function in the ThreadPool.\n\
\n\
run(fn, arg1, arg2, ..., [kwargs[, timeout]]) -> result or none or exception raised\n\
\n\
The default timeout is 'None' which means to block for a result indefinitely.\n\
To block only for a specified time, use timeout=<float_seconds>. To not block at\n\
all and to ignore any results, use a timeout value of 0.\n\
\n\
The keyword 'kwargs' may be passed with a dict that will pass through to 'fn'.\n\
\n\
'fn' must match accept the keywords: 'shutdown'... and 'kwargs', if called with them\n\
\n\
E.g.:\n\
\n\
def fn(*args, kwargs=None, shutdown=None):\n\
    pass\n\
\n\
'shutdown' will be passed a True arg if the thread pool is trying to be shut down quickly.\n\
'kwargs' will be the value of 'kwargs' passed to run().");
static PyObject *_thrpool_run(PyFilThrPool *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = { "kwargs", "timeout", NULL };
    PyObject *method;
    PyObject *method_args;
    PyObject *res;
    Py_ssize_t args_len;
    PyObject *timeout_obj = NULL;
    double timeout;
    int err;
    PyObject *mkwargs = NULL;
    FilWaiter *waiter = NULL;
    PyFilThrPoolRunInfo *info;
    struct timespec tsbuf, *ts = NULL;

    args_len = PyTuple_GET_SIZE(args);
    if (!args_len)
    {
        PyErr_SetString(PyExc_TypeError,
                        "run() takes at least 1 argument");
        return NULL;
    }

    method = PyTuple_GET_ITEM(args, 0);
    if (!PyCallable_Check(method))
    {
        PyErr_SetString(PyExc_TypeError,
                        "run() first argument should be a callable");
        return NULL;
    }

    if (!PyArg_ParseTupleAndKeywords(_EMPTY_TUPLE, kwargs, "|O!O;run() called with invalid kwargs", keywords,
                &PyDict_Type, &mkwargs, &timeout_obj))
    {
        return NULL;
    }

    if (fil_double_from_timeout_obj(timeout_obj, &timeout) < 0)
    {
        return NULL;
    }

    if (timeout != 0.0 && fil_timespec_from_double_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    if (self->is_shutdown)
    {
        PyErr_SetString(PyExc_RuntimeError, "ThreadPool is (or is being) shutdown and cannot run anything.");
        return NULL;
    }

    method_args = PyTuple_GetSlice(args, 1, args_len);
    if (method_args == NULL)
    {
        return NULL;
    }

    if (timeout != 0.0)
    {
        waiter = fil_waiter_alloc();
        if (waiter == NULL)
        {
            Py_DECREF(method_args);
            return NULL;
        }
    }

    info = calloc(1, sizeof(*info));
    if (info == NULL)
    {
        PyErr_SetString(PyExc_MemoryError, "out of memory");
        Py_DECREF(method_args);
        if (waiter != NULL)
        {
            fil_waiter_decref(waiter);
        }
        return NULL;
    }

    Py_INCREF(method);
    Py_XINCREF(mkwargs);

    info->method = method;
    info->args = method_args;
    info->kwargs = mkwargs;
    info->waiter = waiter;
    if (ts != NULL)
    {
        /* The waiter will be a TIMED wait; the worker must then signal it
         * with the GIL held (see _thrpool_run_async). */
        info->flags |= PYFIL_THRPOOL_RUN_INFO_FLAGS_TIMED;
    }

    err = fil_thrpool_run(self->tpool, (FilThrPoolCallback)_thrpool_run_async, info);
    if (err)
    {
        Py_DECREF(method);
        Py_DECREF(method_args);
        Py_XDECREF(mkwargs);
        if (waiter != NULL)
        {
            fil_waiter_decref(waiter);
        }
        free(info);
        PyErr_SetString(PyExc_MemoryError, "out of memory creating ThreadPool entry");
        return NULL;
    }

    if (waiter == NULL)
    {
        Py_RETURN_NONE;
    }

    err = fil_waiter_wait(waiter, ts, NULL);
    fil_waiter_decref(waiter);
    if (err)
    {
        /*
         * not signaled, so nothing has run into background yet.
         * let it free 'info' and not access 'waiter'
         */
        info->flags |= PYFIL_THRPOOL_RUN_INFO_FLAGS_CANCEL;
        return NULL;
    }

    if (info->flags & PYFIL_THRPOOL_RUN_INFO_FLAGS_FAILURE)
    {
        /* no results set to decrement */
        PyErr_SetString(PyExc_MemoryError, "out of memory initializing ThreadPool thread");
        free(info);
        return NULL;
    }

    if (info->flags & PYFIL_THRPOOL_RUN_INFO_FLAGS_EXC)
    {
        PyErr_Restore(info->res_or_exc_type, info->exc_value, info->exc_tb);
        free(info);
        return NULL;
    }

    res = info->res_or_exc_type;

    free(info);

    return res;
}

PyDoc_STRVAR(_thrpool_shutdown_doc,
"Shut the tpool down.\n\
\n\
By default, all queued callbacks will be completed normally before shutting\n\
down.\n\
\n\
If 'now=True' is passed, all queued callbacks will still be called, but with a\n\
shutdown=True keyword argument.\n\
\n\
Also by default, the shutdown will happen in the background. If 'wait=True' is\n\
passed, shutdown() will block until the shutdown is completed.");
static PyObject *_thrpool_shutdown(PyFilThrPool *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = { "now", "wait", NULL };
    int now = 0, wait = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|ii:shutdown", keywords, &now, &wait))
    {
        return NULL;
    }

    if (self->is_shutdown)
    {
        PyErr_SetString(PyExc_RuntimeError, "shutdown() has already been called");
        return NULL;
    }

    if (_thrpool_shutdown_async(self, now, wait, 0))
    {
        return NULL;
    }

    Py_RETURN_NONE;
}

static PyMethodDef _thrpool_methods[] = {
    { "run", (PyCFunction)_thrpool_run, METH_VARARGS|METH_KEYWORDS, _thrpool_run_doc },
    { "shutdown", (PyCFunction)_thrpool_shutdown, METH_VARARGS|METH_KEYWORDS, _thrpool_shutdown_doc },
    { NULL, NULL }
};

static PyMemberDef _thrpool_memberlist[] = {
    { "is_shutdown", T_BOOL, offsetof(PyFilThrPool, is_shutdown), READONLY, "is the pool shutdown?" },
    { NULL, },
};

static PyTypeObject _thrpool_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "_filament.thrpool.ThreadPool",             /* tp_name */
    sizeof(PyFilThrPool),                       /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_thrpool_dealloc,               /* tp_dealloc */
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
    _thrpool_methods,                           /* tp_methods */
    _thrpool_memberlist,                        /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_thrpool_init,                    /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_thrpool_new,                      /* tp_new */
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

/*
 * Shut down every pool that is still live, while the interpreter is still
 * fully alive.  Runs from Python's 'atexit', i.e. from Py_FinalizeEx() but
 * *before* any teardown has happened: threads can still attach a thread
 * state, the GIL is still handed around normally, and the filament scheduler
 * still runs.  A blocking (now=1, wait=1) shutdown here is what keeps
 * _thrpool_dealloc from ever having to attempt one during finalization, where
 * it could not possibly complete.
 *
 * Note the reference dance: the registry only borrows (see above), and the
 * shutdown below blocks -- it parks this greenthread on the scheduler, so
 * other greenthreads run and other pools may be deallocated meanwhile.  We
 * therefore unlink the pool and take a real reference *before* blocking, and
 * we never hold a 'next' pointer across the blocking call: each iteration
 * re-reads the head.  Unlinking first also guarantees termination if a
 * shutdown fails, and makes the whole callback idempotent.
 */
static PyObject *_thrpool_atexit(PyObject *self, PyObject *ignored)
{
    (void)self;
    (void)ignored;

    while (_thrpool_registry != NULL)
    {
        PyFilThrPool *pool = _thrpool_registry;

        Py_INCREF(pool);
        _thrpool_registry_remove(pool);

        if (pool->tpool != NULL && !pool->is_shutdown)
        {
            if (_thrpool_shutdown_async(pool, 1, 1, 0) != 0)
            {
                /* Out of memory, or an exception (a signal) interrupted the
                 * wait.  Nothing useful to do at exit time: report nothing
                 * and move on, rather than letting an exception escape an
                 * atexit callback and print a traceback. */
                PyErr_Clear();
            }
        }

        Py_DECREF(pool);
    }

    Py_RETURN_NONE;
}

static PyMethodDef _thrpool_atexit_def = {
    "_fil_thrpool_shutdown_all", (PyCFunction)_thrpool_atexit,
    METH_NOARGS, NULL
};

/* Register _thrpool_atexit() with the Python 'atexit' module.  Called once,
 * from module init, with the GIL held. */
static int _thrpool_register_atexit(void)
{
    PyObject *cb;
    PyObject *atexit_mod;
    PyObject *res;

    cb = PyCFunction_NewEx(&_thrpool_atexit_def, NULL, NULL);
    if (cb == NULL)
    {
        return -1;
    }

    atexit_mod = PyImport_ImportModule("atexit");
    if (atexit_mod == NULL)
    {
        Py_DECREF(cb);
        return -1;
    }

    res = PyObject_CallMethod(atexit_mod, "register", "O", cb);
    Py_DECREF(atexit_mod);
    Py_DECREF(cb);
    if (res == NULL)
    {
        return -1;
    }
    Py_DECREF(res);
    return 0;
}

PyDoc_STRVAR(_fil_thrpool_module_doc, "Filament _filament.thrpool module.");
static PyMethodDef _fil_thrpool_module_methods[] = {
    { NULL, },
};

_FIL_MODULE_INIT_FN_NAME(thrpool)
{
    PyObject *m;

    PyFilCore_Import();
    PyEval_InitThreads();

    if (_EMPTY_TUPLE == NULL)
    {
        _EMPTY_TUPLE = fil_empty_tuple();
    }

    _FIL_MODULE_SET(m, "_filament.thrpool", _fil_thrpool_module_methods, _fil_thrpool_module_doc);
    if (m == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    if (PyType_Ready(&_thrpool_type) < 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    Py_INCREF((PyObject *)&_thrpool_type);
    if (PyModule_AddObject(m, "ThreadPool", (PyObject *)&_thrpool_type) != 0)
    {
        Py_DECREF((PyObject *)&_thrpool_type);
        return _FIL_MODULE_INIT_ERROR;
    }

    if (_thrpool_register_atexit() < 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    return _FIL_MODULE_INIT_SUCCESS(m);
}
