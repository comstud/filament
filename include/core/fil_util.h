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
#ifndef __FIL_UTIL_H__
#define __FIL_UTIL_H__

#include "core/filament.h"

#define FIL_DEFAULT_TPFLAGS (Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE)

#if defined(EWOULDBLOCK) && EWOULDBLOCK != EAGAIN
#define FIL_IS_EAGAIN(__x) (((__x) == EAGAIN) || ((__x) == EWOULDBLOCK))
#else
#define FIL_IS_EAGAIN(__x) ((__x) == EAGAIN)
#endif

#define FIL_MIN_NANOSECOND_WAIT 250000000L

#define FIL_TIMESPEC_COMPARE(__x, __y, __cmp)                   \
        (((__x)->tv_sec == (__y)->tv_sec) ?                 \
                 ((__x)->tv_nsec __cmp (__y)->tv_nsec) :    \
                 ((__x)->tv_sec __cmp (__y)->tv_sec))

static PyObject *_FIL_EMPTY_TUPLE;

static inline void fil_timespec_now(struct timespec *ts_buf)
{
    struct timeval t;
    int err;

    err = clock_gettime(CLOCK_REALTIME, ts_buf);
    if (err == 0)
    {
        return;
    }

    gettimeofday(&t, NULL);
    ts_buf->tv_sec = t.tv_sec;
    ts_buf->tv_nsec = t.tv_usec * 1000;
}

static inline int fil_double_from_timeout_obj(PyObject *timeoutobj, double *dbl)
{
    if (dbl == NULL)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "double from timeout_obj called with NULL return value");
        return -1;
    }
    if (timeoutobj == Py_None || timeoutobj == NULL)
    {
        *dbl = -1.0;
        return 0;
    }

    if (!PyNumber_Check(timeoutobj))
    {
        PyErr_SetString(PyExc_TypeError,
                        "timeout must be None or a non-negative number");
        return -1;
    }

    *dbl = PyFloat_AsDouble(timeoutobj);
    if (*dbl < 0)
    {
        if (!PyErr_Occurred())
        {
            PyErr_SetString(PyExc_ValueError,
                "timeout must None or a non-negative number");
        }
        return -1;
    }

    return 0;
}

static inline int _fil_ts_from_double(double timeout, struct timespec *ts_buf, struct timespec **ts_ret)
{
    long sec;
    long nsec;

    if (timeout > (double)LONG_MAX)
    {
        PyErr_SetString(PyExc_OverflowError,
                        "timeout period too long");
        return -1;
    }

    fil_timespec_now(ts_buf);

    sec = (long)timeout;
    nsec = (timeout - (double)sec) * 1E9;

    if (ts_buf->tv_nsec < (1000000000L - nsec))
    {
        ts_buf->tv_nsec += nsec;
    }
    else
    {
        ts_buf->tv_sec++;
        ts_buf->tv_nsec -= 1000000000L - nsec;
    }

    if (ts_buf->tv_sec + sec < ts_buf->tv_sec)
    {
        PyErr_SetString(PyExc_OverflowError,
                        "timeout period too long");
        return -1;
    }

    ts_buf->tv_sec += sec;
    *ts_ret = ts_buf;

    return 0;
}

static inline int fil_timespec_from_double_interval(double timeout, struct timespec *ts_buf, struct timespec **ts_ret)
{
    if (timeout < 0.0)
    {
        *ts_ret = NULL;
        return 0;
    }

    return _fil_ts_from_double(timeout, ts_buf, ts_ret);
}

static inline int fil_timespec_from_pyobj_interval(PyObject *timeoutobj, struct timespec *ts_buf, struct timespec **ts_ret)
{
    double timeout;

    if (fil_double_from_timeout_obj(timeoutobj, &timeout) < 0)
    {
        return -1;
    }

    if (timeout < 0.0)
    {
        *ts_ret = NULL;
        return 0;
    }

    return _fil_ts_from_double(timeout, ts_buf, ts_ret);
}

#ifdef _FIL_PYTHON3
/*
 * Minimal argument parser for METH_FASTCALL|METH_KEYWORDS entry points.
 *
 * The hot filament primitives (Semaphore.acquire, Queue.put/get, ...) were
 * profiled spending more cycles inside PyArg_ParseTupleAndKeywords (format
 * string scanning, keyword dict handling) and the METH_VARARGS tuple build
 * than in their actual C bodies.  On Python 3 we accept the vectorcall
 * calling convention and parse by hand; Python 2 keeps the original
 * METH_VARARGS|METH_KEYWORDS implementations (see the per-file #ifdefs).
 *
 * 'names' lists all 'nmax' parameter names in positional order; the first
 * 'nreq' are required.  On success out[0..nmax-1] hold borrowed references
 * (NULL where an optional argument was not given).
 */
static inline int fil_fastcall_parse(PyObject *const *args, Py_ssize_t nargs,
                                     PyObject *kwnames, const char *fname,
                                     Py_ssize_t nreq, Py_ssize_t nmax,
                                     const char * const *names,
                                     PyObject **out)
{
    Py_ssize_t i;

    if (nargs > nmax)
    {
        PyErr_Format(PyExc_TypeError,
                     "%s() takes at most %zd arguments (%zd given)",
                     fname, nmax, nargs);
        return -1;
    }
    for (i = 0; i < nmax; i++)
    {
        out[i] = (i < nargs) ? args[i] : NULL;
    }
    if (kwnames != NULL)
    {
        Py_ssize_t nkw = PyTuple_GET_SIZE(kwnames);
        Py_ssize_t k;

        for (k = 0; k < nkw; k++)
        {
            PyObject *name = PyTuple_GET_ITEM(kwnames, k);
            PyObject *value = args[nargs + k];

            for (i = 0; i < nmax; i++)
            {
                if (PyUnicode_CompareWithASCIIString(name, names[i]) == 0)
                {
                    break;
                }
            }
            if (i == nmax)
            {
                PyErr_Format(PyExc_TypeError,
                             "%s() got an unexpected keyword argument '%U'",
                             fname, name);
                return -1;
            }
            if (out[i] != NULL)
            {
                PyErr_Format(PyExc_TypeError,
                             "%s() got multiple values for argument '%s'",
                             fname, names[i]);
                return -1;
            }
            out[i] = value;
        }
    }
    for (i = 0; i < nreq; i++)
    {
        if (out[i] == NULL)
        {
            PyErr_Format(PyExc_TypeError,
                         "%s() missing required argument '%s'",
                         fname, names[i]);
            return -1;
        }
    }
    return 0;
}
#endif /* _FIL_PYTHON3 */

/*
 * block for a minimum amount of time and simulate an EINTR
 * if the real timeout has not been reached. This allows us to
 * check for exceptions on signals (like KeyboardInterrupt) within
 * a reasonable amount of time and not hang the process.
 */
static inline int fil_pthread_cond_wait_min(pthread_cond_t *cond, pthread_mutex_t *mutex, struct timespec *ts)
{
    struct timespec ts_buf;
    struct timespec *tsptr = &ts_buf;
    int err;

    fil_timespec_now(&ts_buf);
    if (ts_buf.tv_nsec < (1000000000L - FIL_MIN_NANOSECOND_WAIT))
    {
        ts_buf.tv_nsec += FIL_MIN_NANOSECOND_WAIT;
    }
    else
    {
        ts_buf.tv_sec++;
        ts_buf.tv_nsec -= 1000000000L - FIL_MIN_NANOSECOND_WAIT;
    }

    if (ts != NULL && FIL_TIMESPEC_COMPARE(tsptr, ts, >))
    {
        tsptr = ts;
    }

    err = pthread_cond_timedwait(cond, mutex, tsptr);
    if (err == ETIMEDOUT && tsptr != ts)
    {
        err = EINTR;
    }

    return err;
}

/*
 * Return a stable identity for the currently running execution context,
 * used for re-entrant lock (RLock) ownership tracking.
 *
 * The identity combines the OS thread id with the current greenlet object
 * pointer:
 *   - Two greenlets on the SAME thread have different object pointers, so they
 *     get different ids (RLock must distinguish them).
 *   - Greenlets on DIFFERENT threads differ by thread id, so even if two
 *     greenlet objects ever shared an address across threads the ids differ.
 *   - For a given live greenlet the id is stable across calls.
 *
 * Note on lifetime: PyGreenlet_GetCurrent() returns a NEW reference, but the
 * greenlet runtime independently keeps the *current* greenlet alive for the
 * duration of this call, so reading its pointer before we drop our temporary
 * reference is safe -- our Py_DECREF cannot free the object out from under us.
 * (The previous implementation returned the bare pointer and relied on that
 * same fact, but without folding in the thread id the value was ambiguous and
 * more prone to cross-thread aliasing.) The only residual caveat -- a greenlet
 * that dies while still holding an RLock, whose address is later reused on the
 * same thread -- is a pre-existing "released-by-death" hazard shared with
 * CPython's thread-ident-based RLock and is out of scope here.
 */
static inline uint64_t fil_get_ident(void)
{
    PyGreenlet *gl;
    uint64_t result;

    if (_PyGreenlet_API == NULL)
    {
        PyGreenlet_Import();
    }

    gl = PyGreenlet_GetCurrent();
    if (gl == NULL)
    {
        /*
         * PyGreenlet_GetCurrent() fails once the interpreter is tearing down:
         * it returns NULL with RuntimeError("greenlet is being finalized")
         * set.  This function computes an id and cannot report an error, so
         * that exception must not survive the call -- any caller left holding
         * it hands CPython a result with an error set, which becomes
         * "SystemError: ... returned a result with an exception set" at every
         * lock touched from a __del__ or a weakref callback at shutdown.
         *
         * Nothing of the caller's is lost by clearing: no caller gets here
         * with an exception pending, and greenlet has already replaced it if
         * one somehow were.  With no greenlet to fold in, the id degenerates
         * to the thread id, which is fine -- by then the thread has exactly
         * one execution context left.
         */
        PyErr_Clear();
    }
    result = ((uint64_t)PyThread_get_thread_ident() << 1) ^ (uint64_t)(uintptr_t)gl;
    Py_XDECREF(gl);

    return result;
}

static inline PyObject *fil_create_module(char *name)
{
    PyObject *modules = PyImport_GetModuleDict();
    PyObject *m;

    if ((m = PyDict_GetItemString(modules, name)) != NULL)
    {
        PyErr_SetString(PyExc_ValueError, "Module already exists");
        return NULL;
    }

    m = PyModule_New(name);
    if (m == NULL)
    {
        return NULL;
    }

    if (PyDict_SetItemString(modules, name, m) != 0)
    {
        Py_DECREF(m);
        return NULL;
    }
    return m;
}

static inline PyObject *fil_empty_tuple(void)
{
    if (_FIL_EMPTY_TUPLE == NULL)
    {
        _FIL_EMPTY_TUPLE = PyTuple_New(0);
    }
    return _FIL_EMPTY_TUPLE;
}

static inline PyObject *fil_format_exception(PyObject *exc_type, PyObject *exc_value, PyObject *exc_tb)
{
    PyObject *tb_mod;
    PyObject *format_exc;
    PyObject *res;

    tb_mod = PyImport_ImportModuleNoBlock("traceback");
    if (tb_mod == NULL)
    {
        return NULL;
    }

    format_exc = PyObject_GetAttrString(tb_mod, "format_exception");

    Py_DECREF(tb_mod);

    if (format_exc == NULL)
    {
        return NULL;
    }

    if (!PyCallable_Check(format_exc))
    {
        Py_DECREF(format_exc);
        PyErr_SetString(PyExc_RuntimeError, "traceback.format_exception not callable");
        return NULL;
    }

    res = PyObject_CallFunctionObjArgs(format_exc, exc_type, exc_value, exc_tb, NULL);

    Py_DECREF(format_exc);
    return res;
}

static inline void fil_set_timeout_exc(PyObject *timeout_exc)
{
    PyObject *exc_type = PyFil_TimeoutExc;
    PyObject *exc_value = NULL;
    PyObject *exc_tb = NULL;

    if (timeout_exc == NULL)
    {
        PyErr_SetString(exc_type, "timed out");
        return;
    }
    else if (PyExceptionClass_Check(timeout_exc))
    {
        PyErr_SetString(timeout_exc, "timed out");
        return;
    }
    else if (PyExceptionInstance_Check(timeout_exc))
    {
        exc_value = timeout_exc;
    }
    else if (PyCallable_Check(timeout_exc))
    {
        exc_value = PyObject_Call(timeout_exc, fil_empty_tuple(), NULL);
        if (exc_value == NULL)
        {
            /* just leave this one */
            return;
        }
        PyErr_SetString(PyExc_TypeError, "timeout_exc callback should always raise");
        Py_DECREF(exc_value);
        return;
    }
    else
    {
        PyErr_SetString(PyExc_TypeError,
                        "timeout_exc must be an exception class, an exception "
                        "instance, or a callable that raises");
        return;
    }
    /* PyErr_Restore steals a reference to each argument; 'exc_value' is the
     * caller's borrowed timeout_exc and 'exc_type' is a borrowed Py_TYPE, so
     * both must be incref'd here or every timeout that gets raised and
     * cleared walks the instance's (and its class's) refcount down by one --
     * a use-after-free once it hits zero with live holders. */
    exc_type = PyExceptionInstance_Class(exc_value);
    Py_INCREF(exc_type);
    Py_INCREF(exc_value);
    PyErr_Restore(exc_type, exc_value, exc_tb);
}

#endif /* __FIL_UTIL_H__ */
