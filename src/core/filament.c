/*
 * The MIT License (MIT): http://opensource.org/licenses/mit-license.php
 *
 * Copyright (c) 2013-2019, Chris Behrens
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

#define __FIL_BUILDING_CORE__
#include "core/filament.h"
#ifdef _POSIX_PRIORITY_SCHEDULING
#include <sched.h>
#endif

static PyFilCore_CAPIObject _PY_FIL_CORE_API_STORAGE;

PyFilCore_CAPIObject *_PY_FIL_CORE_API = &_PY_FIL_CORE_API_STORAGE;
PyTypeObject *PyFilament_Type = NULL;

static PyObject *_fil_filament_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    /*
     * greenlet 3.x made PyGreenlet an opaque object whose real state lives
     * in a C++ object referenced by the (private) 'pimpl' pointer. That
     * pointer is constructed *only* by greenlet's own tp_new (green_new) --
     * NOT by tp_alloc. In greenlet 0.4.x the struct was plain C and a bare
     * tp_alloc zero-initialised every field, so this function used to just
     * call tp_alloc. On 3.x that leaves pimpl == NULL and the very next
     * greenlet operation (green_init -> green_setrun) dereferences it and
     * segfaults. We MUST delegate to greenlet's tp_new so pimpl is built.
     * green_new ignores its args/kwds (it uses empty tuple/dict internally),
     * so forwarding NULLs is safe.
     */
    return PyGreenlet_Type.tp_new(type, args, kwargs);
}

static int _fil_filament_init_common(PyFilament *self, PyObject *method, PyObject *args, PyObject *kwargs)
{
    /* Returns -1 on error */
    PyObject *main_method;
    PyGreenlet *sched_greenlet;

    if (!PyCallable_Check(method))
    {
        PyErr_SetString(PyExc_TypeError,
                        "Filament() method must be a callable");
        return -1;
    }

    if (self->sched != NULL)
    {
        PyErr_SetString(PyExc_RuntimeError, "__init__() already called");
        return -1;
    }

    Py_INCREF(method);
    Py_INCREF(args);
    Py_XINCREF(kwargs);

    self->method = method;
    self->method_args = args;
    self->method_kwargs = kwargs;
    /* going forward, the above will be defrefed on dealloc */

    main_method = PyObject_GetAttrString((PyObject *)self, "main");
    if (main_method == NULL)
    {
        return -1;
    }

    self->sched = fil_scheduler_get(1);
    if (self->sched == NULL)
    {
        Py_DECREF(main_method);
        return -1;
    }

    self->message = fil_message_alloc();
    if (self->message == NULL)
    {
        Py_DECREF(main_method);
        return -1;
    }

    sched_greenlet = fil_scheduler_greenlet(self->sched);

    PyObject *gl_args = PyTuple_Pack(2, main_method, sched_greenlet);

    Py_DECREF(main_method);

    if (PyGreenlet_Type.tp_init((PyObject *)self, gl_args, NULL) < 0)
    {
        Py_DECREF(gl_args);
        return -1;
    }

    Py_DECREF(gl_args);

    /* Enqueue the initial switch into this filament. Propagate failure so a
     * half-constructed filament (whose body will never be scheduled) isn't
     * handed back as if it were live. */
    if (fil_scheduler_gl_switch(self->sched, NULL, (PyGreenlet *)self) < 0)
    {
        return -1;
    }

    return 0;
}

static int _fil_filament_init(PyFilament *self, PyObject *args, PyObject *kwargs)
{
    PyObject *method;
    PyObject *method_args;
    int result;
    Py_ssize_t args_len;

    args_len = PyTuple_GET_SIZE(args);
    if (!args_len)
    {
        PyErr_SetString(PyExc_TypeError,
                        "Filament() takes at least 1 argument");
        return -1;
    }

    method = PyTuple_GET_ITEM(args, 0);
    method_args = PyTuple_GetSlice(args, 1, args_len);
    if (method_args == NULL)
    {
        return -1;
    }

    result = _fil_filament_init_common(self, method, method_args, kwargs);
    Py_DECREF(method_args);
    return result;
}

/*
 * GC support.
 *
 * Filament inherits Py_TPFLAGS_HAVE_GC from greenlet, but greenlet's
 * green_traverse() knows nothing about the PyFilament fields tacked on after
 * the embedded PyGreenlet -- most importantly 'method'. That matters because
 * the natural way to wrap a greenthread is to spawn a *bound method* of the
 * wrapper and store the resulting Filament back on the wrapper:
 *
 *     g._filament = filament.spawn(g._target)
 *
 * which is exactly what gevent_compat.Greenlet, eventlet_compat.GreenThread
 * and StreamServer's per-connection handler all do. That closes the cycle
 *
 *     Filament.method -> bound method -> wrapper -> wrapper._filament -> Filament
 *
 * Without a traverse that visits 'method', the collector cannot see the cycle
 * at all, so it is not merely uncollected but invisible: the whole cluster
 * (wrapper, its args, its Event, any socket it holds) leaks forever. Being
 * GC-tracked with a tp_traverse that under-reports our references was also
 * simply incorrect.
 *
 * _fil_filament_main() additionally drops these references the moment the body
 * returns, which breaks the same cycle by plain refcounting and keeps steady
 * state flat without waiting for a collection; traverse/clear are what cover
 * the filaments that never run (spawned then killed) and any cycle we have not
 * thought of.
 */
static int _fil_filament_traverse(PyFilament *self, visitproc visit, void *arg)
{
    Py_VISIT(self->method);
    Py_VISIT(self->method_args);
    Py_VISIT(self->method_kwargs);

    /* Message is GC-tracked (it holds the body's return value, or its
     * exception and traceback), so it must be visited or the collector could
     * decide it is unreachable while we still own it. Deliberately NOT
     * cleared in tp_clear: breaking the cycle is the Message's own tp_clear's
     * job, and a live Filament still needs somewhere to deliver its result.
     *
     * 'sched' is not visited: PyFilScheduler is not GC-tracked and holds no
     * reference that can lead back to user objects. */
    Py_VISIT(self->message);

    if (PyGreenlet_Type.tp_traverse != NULL)
    {
        return PyGreenlet_Type.tp_traverse((PyObject *)self, visit, arg);
    }

    return 0;
}

static int _fil_filament_clear(PyFilament *self)
{
    Py_CLEAR(self->method);
    Py_CLEAR(self->method_args);
    Py_CLEAR(self->method_kwargs);

    if (PyGreenlet_Type.tp_clear != NULL)
    {
        return PyGreenlet_Type.tp_clear((PyObject *)self);
    }

    return 0;
}

static void _fil_filament_dealloc(PyFilament *self)
{
    Py_CLEAR(self->message);
    Py_CLEAR(self->sched);
    Py_CLEAR(self->method);
    Py_CLEAR(self->method_args);
    Py_CLEAR(self->method_kwargs);

    /*
     * Chain to greenlet's tp_dealloc rather than calling tp_free ourselves.
     *
     * greenlet owns per-greenlet state that only green_dealloc() releases: on
     * greenlet 3.x the C++ 'pimpl' object (allocated with PyObject_Malloc, so
     * one ~224-byte block leaked per Filament), plus the weakref list, the
     * instance dict and the GC untrack. The historical comment here worried
     * that greenlet might not be built with GC support and so used tp_free
     * directly -- but green_dealloc ends in exactly that tp_free call itself,
     * having done the rest of the cleanup first, so delegating is both safe
     * and strictly more correct on every greenlet we support.
     *
     * Clearing our fields *first* is safe: green_dealloc can throw
     * GreenletExit into a still-suspended greenlet to unwind it, and that
     * unwinding runs the tail of _fil_filament_main() -- but that function
     * holds its own references to method/args/kwargs for the duration of the
     * call and NULL-checks 'message', precisely so this ordering is legal.
     */
    PyGreenlet_Type.tp_dealloc((PyObject *)self);
}

PyDoc_STRVAR(_fil_filament_wait_doc, "Wait!");
static PyObject *_fil_filament_wait(PyFilament *self, PyObject *args)
{
    return fil_message_wait(self->message, NULL);
}

PyDoc_STRVAR(_fil_filament_main_doc, "Main entrypoint for the Filament.");
static PyObject *_fil_filament_main(PyFilament *self, PyObject *args)
{
    PyObject *result;
    /*
     * Take our own references for the duration of the call. PyObject_Call()
     * only borrows its arguments, so without this the callee could drop the
     * last reference to us -- clearing self->method out from under an
     * in-flight call -- simply by discarding the wrapper object that owns the
     * bound method we are running. It also makes it legal for tp_dealloc to
     * clear these fields before unwinding a suspended greenlet.
     */
    PyObject *method = self->method;
    PyObject *method_args = self->method_args;
    PyObject *method_kwargs = self->method_kwargs;

    Py_XINCREF(method);
    Py_XINCREF(method_args);
    Py_XINCREF(method_kwargs);

    result = PyObject_Call(method, method_args, method_kwargs);

    Py_XDECREF(method);
    Py_XDECREF(method_args);
    Py_XDECREF(method_kwargs);

    /*
     * The body has run and can never run again, so release the callable and
     * its arguments now instead of waiting for dealloc. This is what breaks,
     * by plain refcounting, the
     *
     *     Filament.method -> bound method -> wrapper -> wrapper._filament
     *
     * cycle that every compat shim creates (see the comment on
     * _fil_filament_traverse). Doing it here rather than relying on the
     * cyclic collector keeps memory flat under sustained load, where the
     * allocation rate easily outruns collection.
     *
     * Note these are already NULL if tp_clear ran first; Py_CLEAR copes.
     */
    Py_CLEAR(self->method);
    Py_CLEAR(self->method_args);
    Py_CLEAR(self->method_kwargs);

    if (result == NULL)
    {
        PyObject *exc_type, *exc_value, *exc_tb;

        PyErr_Fetch(&exc_type, &exc_value, &exc_tb);

        if (exc_type == NULL)
        {
            /* Just in case, but these should be NULL also */
            Py_XDECREF(exc_value);
            Py_XDECREF(exc_tb);
            PyErr_SetString(PyExc_RuntimeError,
                            "Filament method returned NULL, but with "
                            "no exception");
            return NULL;
        }

        /* 'message' is NULL only when tp_dealloc cleared it and then had
         * greenlet unwind us with a GreenletExit; there is by definition
         * nobody left to deliver the result to in that case. */
        if (self->message != NULL)
        {
            fil_message_send_exception(self->message, exc_type, exc_value,
                                       exc_tb);
        }
        /* Restore this so the scheduler can catch and force the main
         * greenlet to raise if they are system exceptions.
         */
        PyErr_Restore(exc_type, exc_value, exc_tb);
        return NULL;
    }
    else
    {
        if (self->message != NULL)
        {
            fil_message_send(self->message, result);
        }
        Py_DECREF(result);
    }

    Py_RETURN_NONE;
}

static PyMethodDef _fil_filament_methods[] = {
    {"wait", (PyCFunction)_fil_filament_wait, METH_VARARGS, _fil_filament_wait_doc},
    {"join", (PyCFunction)_fil_filament_wait, METH_VARARGS, _fil_filament_wait_doc},
    {"main", (PyCFunction)_fil_filament_main, METH_NOARGS, _fil_filament_main_doc},
    { NULL, NULL }
};

static PyTypeObject _fil_filament_type = {
    PyVarObject_HEAD_INIT(0, 0)                 /* Must fill in type
                                                   value later */
    "_filament.Filament",                       /* tp_name */
    sizeof(PyFilament),                         /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_fil_filament_dealloc,          /* tp_dealloc */
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
    /* Py_TPFLAGS_HAVE_GC must be spelled out here: PyType_Ready() only
     * inherits it from the base when tp_traverse AND tp_clear are both NULL,
     * which they no longer are. */
    FIL_DEFAULT_TPFLAGS|Py_TPFLAGS_HAVE_GC,     /* tp_flags */
    0,                                          /* tp_doc */
    (traverseproc)_fil_filament_traverse,       /* tp_traverse */
    (inquiry)_fil_filament_clear,               /* tp_clear */
    0,                                          /* tp_richcompare */
    0,                                          /* tp_weaklistoffset */
    0,                                          /* tp_iter */
    0,                                          /* tp_iternext */
    _fil_filament_methods,                      /* tp_methods */
    0,                                          /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_fil_filament_init,               /* tp_init */
    0,                                          /* tp_alloc */
    (newfunc)_fil_filament_new,                 /* tp_new */
    0,                                          /* tp_free */
    0,                                          /* tp_is_gc */
    0,                                          /* tp_bases */
    0,                                          /* tp_mro */
    0,                                          /* tp_cache */
    0,                                          /* tp_subclasses */
    0,                                          /* tp_weaklist */
    0,                                          /* tp_del */
    0,                                          /* tp_version_tag */
};

/* Cached int(0) singleton for the sleep(0) fast path.  Set once at module
 * init and never released.  CPython (2 and 3) interns small ints, so the
 * common literal sleep(0) call hits a single pointer compare. */
static PyObject *_fil_int_zero;

PyDoc_STRVAR(_fil_sleep_doc, "Sleep!");
static PyObject *_fil_sleep(PyObject *_self, PyObject *timeout)
{
    PyGreenlet *current_gl;
    PyFilScheduler *fil_scheduler;
    FilWaiter *waiter;
    struct timespec tsbuf;
    struct timespec *ts;
    int err;

    /* Fast path for the extremely common cooperative-yield idiom sleep(0):
     * skip the number->double->timespec conversion AND the clock_gettime.
     * An immediate (ts == NULL) event is FIFO with other immediate wakeups
     * and runs before any event carrying a real timestamp, which preserves
     * "run me on the next scheduler pass" semantics. */
    if (timeout == _fil_int_zero)
    {
        fil_scheduler = fil_scheduler_get(0);
        if (fil_scheduler != NULL)
        {
            current_gl = PyGreenlet_GetCurrent();
            if (current_gl == NULL)
            {
                Py_DECREF(fil_scheduler);
                return NULL;
            }
            if (fil_scheduler_gl_switch(fil_scheduler, NULL, current_gl) < 0)
            {
                Py_DECREF(current_gl);
                Py_DECREF(fil_scheduler);
                return NULL;
            }
            Py_DECREF(current_gl);
            fil_scheduler_switch(fil_scheduler);
            if (PyErr_Occurred())
            {
                Py_DECREF(fil_scheduler);
                return NULL;
            }
            Py_DECREF(fil_scheduler);
            Py_RETURN_NONE;
        }
        /* No scheduler on this thread: fall through to the generic path,
         * which handles OS-thread sleeps. */
    }

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    if (ts == NULL)
    {
        PyErr_SetString(PyExc_TypeError, "argument must be a number");
        return NULL;
    }

    fil_scheduler = fil_scheduler_get(0);
    if (fil_scheduler == NULL)
    {
        pthread_mutex_t l;
        pthread_cond_t c;
        int err = 0;
        PyThreadState *thr_state;

        thr_state = PyEval_SaveThread();

        pthread_mutex_init(&l, NULL);
        pthread_cond_init(&c, NULL);
        pthread_mutex_lock(&l);

        for(;;)
        {
            err = fil_pthread_cond_wait_min(&c, &l, ts);
            PyEval_RestoreThread(thr_state);
            if (err == ETIMEDOUT || PyErr_CheckSignals())
            {
                break;
            }

            thr_state = PyEval_SaveThread();
        }

        pthread_mutex_unlock(&l);
        pthread_mutex_destroy(&l);
        pthread_cond_destroy(&c);

        if (err == ETIMEDOUT)
        {
            Py_RETURN_NONE;
        }
        else
        {
            /* exception from signal handler */
            return NULL;
        }
    }

    /*
     * Park on a waiter rather than queueing a bare switch back to ourselves.
     * Nobody will ever signal this waiter, so the wait ends when the deadline
     * arrives -- but going through the waiter means the wakeup is CANCELLED if
     * something else resumes us first (an expiring gevent Timeout, a kill(),
     * any throw).  A bare self-switch would stay queued and later fire into
     * whatever this greenthread went on to do; landing in an unrelated untimed
     * wait, that surfaced as "Empty: timed out" from a queue that had no
     * timeout at all.
     *
     * It also keeps the scheduler's handle on the heap.  On the
     * classic-greenlet builds (2.7, 3.9) a parked greenlet's C stack is copied
     * away and restored on resume, so anything the scheduler writes to an
     * address inside that stack is undone -- a cancellation handle held in a
     * local variable silently stops working there.
     */
    waiter = fil_waiter_alloc();
    if (waiter == NULL)
    {
        Py_DECREF(fil_scheduler);
        return NULL;
    }

    err = fil_waiter_wait(waiter, ts, NULL);
    fil_waiter_decref(waiter);
    Py_DECREF(fil_scheduler);

    if (err == -ETIMEDOUT)
    {
        /* The deadline arriving is the whole point of a sleep, not an error;
         * drop the timeout exception the waiter raised for it. */
        PyErr_Clear();
        Py_RETURN_NONE;
    }

    if (err || PyErr_Occurred())
    {
        return NULL;
    }

    Py_RETURN_NONE;
}

PyDoc_STRVAR(_fil_yield_doc, "Yield control to another thread.");
static PyObject *_fil_yield(PyObject *_self, PyObject *_args)
{
    PyGreenlet *current_gl;
    PyFilScheduler *fil_scheduler;

    fil_scheduler = fil_scheduler_get(0);
    if (fil_scheduler == NULL)
    {
        Py_BEGIN_ALLOW_THREADS
#ifdef _POSIX_PRIORITY_SCHEDULING
        sched_yield();
#else
        /* pthread_yield() is non-standard (glibc removed the public
         * declaration); sched_yield() is the portable POSIX spelling. */
        sched_yield();
#endif
        Py_END_ALLOW_THREADS
        Py_RETURN_NONE;
    }

    current_gl = PyGreenlet_GetCurrent();
    if (current_gl == NULL)
    {
        Py_DECREF(fil_scheduler);
        return NULL;
    }

    if (fil_scheduler_gl_switch(fil_scheduler, NULL, current_gl) < 0)
    {
        Py_DECREF(current_gl);
        Py_DECREF(fil_scheduler);
        return NULL;
    }
    Py_DECREF(current_gl);
    fil_scheduler_switch(fil_scheduler);
    if (PyErr_Occurred())
    {
        Py_DECREF(fil_scheduler);
        return NULL;
    }
    Py_DECREF(fil_scheduler);
    Py_RETURN_NONE;
}

PyDoc_STRVAR(_fil_spawn_doc, "Spawn a Filament.");
static PyFilament *_fil_spawn(PyObject *_self, PyObject *args, PyObject *kwargs)
{
    PyObject *method;
    PyObject *method_args;
    PyFilament *fil;
    Py_ssize_t args_len;

    args_len = PyTuple_GET_SIZE(args);
    if (!args_len)
    {
        PyErr_SetString(PyExc_TypeError,
                        "spawn() takes at least 1 argument");
        return NULL;
    }

    method = PyTuple_GET_ITEM(args, 0);
    if (!PyCallable_Check(method))
    {
        PyErr_SetString(PyExc_TypeError,
                        "spawn() first argument should be a callable");
        return NULL;
    }

    method_args = PyTuple_GetSlice(args, 1, args_len);
    if (method_args == NULL)
    {
        return NULL;
    }

    fil = filament_alloc(method, method_args, kwargs);
    Py_DECREF(method_args);
    return fil;
}

PyDoc_STRVAR(cext_doc, "Filament _filament module.");
static PyMethodDef cext_methods[] = {
    {"sleep", (PyCFunction)_fil_sleep, METH_O, _fil_sleep_doc },
    {"spawn", (PyCFunction)_fil_spawn, METH_VARARGS|METH_KEYWORDS, _fil_spawn_doc },
    {"yield_thread", (PyCFunction)_fil_yield, METH_NOARGS, _fil_yield_doc },
    { NULL, NULL }
};

PyFilament *filament_alloc(PyObject *method, PyObject *args, PyObject *kwargs)
{
    PyFilament *self;

    self = (PyFilament *)_fil_filament_new(&_fil_filament_type, NULL, NULL);
    if (self == NULL)
        return NULL;
    if (_fil_filament_init_common(self, method, args, kwargs) < 0)
    {
        Py_DECREF(self);
        return NULL;
    }
    return self;
}

_FIL_MODULE_INIT_FN_NAME(core)
{
    PyObject *m;
    PyObject *capsule;

    PyGreenlet_Import();
    if (_PyGreenlet_API == NULL)
    {
        /* PyCapsule_Import already set an ImportError (greenlet C API not
         * found).  Bail cleanly instead of NULL-dereferencing PyGreenlet_Type
         * below. */
        return _FIL_MODULE_INIT_ERROR;
    }

    _fil_int_zero = PyInt_FromLong(0);
    if (_fil_int_zero == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    _FIL_MODULE_SET(m, FILAMENT_CORE_MODULE_NAME, cext_methods, cext_doc);
    if (m == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    _fil_filament_type.tp_base = &PyGreenlet_Type;

    /*
     * We declare Py_TPFLAGS_HAVE_GC unconditionally in the type struct, but
     * that is only correct if the greenlet we are subclassing was itself
     * built with GC support: tp_alloc (inherited from greenlet) sizes the
     * allocation from *our* flags, while tp_free (also inherited) frees
     * according to greenlet's. Every greenlet we support does use GC, so this
     * is belt and braces -- but a mismatch would corrupt the heap, so check
     * rather than assume.
     */
    if (!(PyGreenlet_Type.tp_flags & Py_TPFLAGS_HAVE_GC))
    {
        _fil_filament_type.tp_flags &= ~Py_TPFLAGS_HAVE_GC;
        _fil_filament_type.tp_traverse = NULL;
        _fil_filament_type.tp_clear = NULL;
    }

    if (PyType_Ready(&_fil_filament_type) < 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    Py_INCREF((PyObject *)&_fil_filament_type);
    if (PyModule_AddObject(m, "Filament",
                           (PyObject *)&_fil_filament_type) != 0)
    {
        Py_DECREF((PyObject *)&_fil_filament_type);
        return _FIL_MODULE_INIT_ERROR;
    }

    PyFilament_Type = &_fil_filament_type;
    _PY_FIL_CORE_API->filament_type = PyFilament_Type;
    _PY_FIL_CORE_API->filament_alloc = filament_alloc;

    if (fil_message_init(m, _PY_FIL_CORE_API) < 0 ||
        fil_scheduler_init(m, _PY_FIL_CORE_API) < 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    /*
     * Publish the C API capsule BEFORE fil_exceptions_init().
     *
     * fil_exceptions_init() imports 'filament.exc', which runs
     * filament/__init__.py, which imports filament.greenthread, which imports
     * _filament.timer, whose own init calls PyFilCore_Import() -- i.e. it
     * re-enters us while we are still initialising. That sibling extension
     * only stashes the capsule pointer at import time and dereferences it
     * later, so handing it a capsule now is safe: the message and scheduler
     * halves of the struct are already filled in above, and the exception
     * half is filled in immediately below, long before any call can use it.
     *
     * Publishing late instead made 'import _filament.thrpool' (or any other
     * _filament submodule) fail outright on Python 2 when it was the first
     * filament import in the process: PyCapsule_Import() there resolves the
     * dotted name by attribute lookup from the parent package, and neither
     * the capsule nor the 'core' attribute existed yet.
     */
    capsule = PyCapsule_New(_PY_FIL_CORE_API, FILAMENT_CORE_CAPSULE_NAME, NULL);
    if (PyModule_AddObject(m, FILAMENT_CORE_CAPI_NAME, capsule) != 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

#if PY_MAJOR_VERSION < 3
    /*
     * Python 2 only: bind ourselves onto the parent package by hand. The
     * import machinery does that after our init returns, which is too late
     * for the re-entrant PyCapsule_Import() described above (it walks
     * '_filament' -> 'core' -> '_C_API' with getattr). Python 3 resolves the
     * module through sys.modules instead and needs no help.
     */
    {
        PyObject *pkg = PyImport_ImportModule("_filament");

        if (pkg == NULL)
        {
            return _FIL_MODULE_INIT_ERROR;
        }
        if (PyObject_SetAttrString(pkg, "core", m) < 0)
        {
            Py_DECREF(pkg);
            return _FIL_MODULE_INIT_ERROR;
        }
        Py_DECREF(pkg);
    }
#endif

    if (fil_exceptions_init(m, _PY_FIL_CORE_API) < 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    return _FIL_MODULE_INIT_SUCCESS(m);
}

