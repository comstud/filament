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
    FilThrPool *tpool;
} PyFilThrPool;

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

/* this will block, if there are things waiting to run */
static void _thrpool_dealloc(PyFilThrPool *self)
{
    if (self->tpool != NULL && !self->is_shutdown)
    {
        _thrpool_shutdown_async(self, 1, 1, 1);
        return;
    }
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
        PyErr_SetString(PyExc_Exception, "ThreadPool is (or is being) shutdown and cannot run anything.");
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
        PyErr_SetString(PyExc_Exception, "shutdown() has already been called");
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
    "filament.thrpool.ThreadPool",              /* tp_name */
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
    Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE,     /* tp_flags */
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
};

PyDoc_STRVAR(_fil_thrpool_module_doc, "Filament _filament.thrpool module.");
static PyMethodDef _fil_thrpool_module_methods[] = {
    { NULL, },
};

PyMODINIT_FUNC
initthrpool(void)
{
    PyObject *m;

    PyFilCore_Import();
    PyEval_InitThreads();

    if (_EMPTY_TUPLE == NULL)
    {
        _EMPTY_TUPLE = fil_empty_tuple();
    }

    m = Py_InitModule3("_filament.thrpool", _fil_thrpool_module_methods, _fil_thrpool_module_doc);
    if (m == NULL)
    {
        return;
    }

    if (PyType_Ready(&_thrpool_type) < 0)
    {
        return;
    }

    Py_INCREF((PyObject *)&_thrpool_type);
    if (PyModule_AddObject(m, "ThreadPool", (PyObject *)&_thrpool_type) != 0)
    {
        Py_DECREF((PyObject *)&_thrpool_type);
        return;
    }

    return;
}
