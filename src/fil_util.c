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

#include <sys/time.h>
#include <pthread.h>
#include "fil_util.h"
#include "fil_scheduler.h"


int fil_timeoutobj_to_timespec(PyObject *timeoutobj,
                               struct timespec *ts_buf,
                               struct timespec **ts_ret)
{
    double timeout;
    long sec;
    long nsec;
    struct timeval t;

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
    if (timeout == -1 && PyErr_Occurred())
        return -1;

    if (timeout < 0)
    {
        PyErr_SetString(PyExc_ValueError,
                        "timeout must be positive or None");
        return -1;
    }

    if (timeout > (double)LONG_MAX)
    {
        PyErr_SetString(PyExc_OverflowError,
                        "timeout period too long");
        return -1;
    }

    gettimeofday(&t, NULL);
    ts_buf->tv_sec = t.tv_sec;
    ts_buf->tv_nsec = t.tv_usec * 1000;

    sec = (long)timeout;
    nsec = (timeout - (double)sec) * 1E9;

    if (ts_buf->tv_nsec >= (1000000000L - nsec))
    {
        ts_buf->tv_sec++;
        ts_buf->tv_nsec = ts_buf->tv_nsec - (1000000000L - nsec);
    }
    else
    {
        ts_buf->tv_nsec += nsec;
    }

    if (ts_buf->tv_sec > (LONG_MAX - sec))
    {
        PyErr_SetString(PyExc_OverflowError,
                        "timeout period too long");
        return -1;
    }

    ts_buf->tv_sec += sec;
    *ts_ret = ts_buf;

    return 0;
}

uint64_t fil_get_ident()
{
    PyGreenlet *gl;
    
    if (_PyGreenlet_API == NULL)
        PyGreenlet_Import();

    gl = PyGreenlet_GetCurrent();
    uint64_t result = (uint64_t)gl;

    Py_XDECREF(gl);
    return result;
}

PyObject *fil_create_module(char *name)
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
