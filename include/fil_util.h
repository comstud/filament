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


#include <Python.h>
#include <greenlet.h>
#include <sys/time.h>
#include <time.h>
#include <pthread.h>

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

    if (timeoutobj == Py_None || timeoutobj == NULL)
    {
        *ts_ret = NULL;
        return 0;
    }

    if (!PyNumber_Check(timeoutobj))
    {
        return -1;
    }

    timeout = PyFloat_AsDouble(timeoutobj);
    if (timeout < 0 && PyErr_Occurred())
    {
        return -1;
    }

    if (timeout < 0)
    {
        PyErr_SetString(PyExc_ValueError,
                        "timeout must be positive or None");
        return -1;
    }

    return _fil_ts_from_double(timeout, ts_buf, ts_ret);
}

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

static inline uint64_t fil_get_ident(void)
{
    PyGreenlet *gl;
    uint64_t result;

    if (_PyGreenlet_API == NULL)
    {
        PyGreenlet_Import();
    }

    gl = PyGreenlet_GetCurrent();
    result = (uint64_t)gl;
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

#endif /* __FIL_UTIL_H__ */
