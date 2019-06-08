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
#include <unistd.h>
#ifdef _POSIX_PRIORITY_SCHEDULING
#include <sched.h>
#endif
#include <string.h>
#include <sys/time.h>
#include <pthread.h>
#include "filament.h"
#include "fil_lock.h"
#include "fil_cond.h"
#include "fil_scheduler.h"
#include "fil_timer.h"
#include "fil_message.h"
#include "fil_semaphore.h"
#include "fil_util.h"
#include "fil_exceptions.h"
#include "fil_iothread.h"
#include "fil_io.h"
#include "fil_queue.h"


PyDoc_STRVAR(cext_sleep_doc, "Sleep!");
static PyObject *cext_sleep(PyObject *_self, PyObject *args)
{
    PyGreenlet *current_gl;
    PyFilScheduler *fil_scheduler;
    struct timespec tsbuf;
    struct timespec *ts;
    PyObject *timeout;

    if (!PyArg_ParseTuple(args, "O", &timeout))
    {
        return NULL;
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

    current_gl = PyGreenlet_GetCurrent();
    if (current_gl == NULL)
        return NULL;

    fil_scheduler_gl_switch(fil_scheduler, ts, current_gl);
    Py_DECREF(current_gl);
    fil_scheduler_switch(fil_scheduler);

    if (PyErr_Occurred())
        return NULL;

    Py_RETURN_NONE;
}

PyDoc_STRVAR(cext_yield_doc, "Yield control to another thread.");
static PyObject *cext_yield(PyObject *_self, PyObject *_args)
{
    PyGreenlet *current_gl;
    PyFilScheduler *fil_scheduler;

    fil_scheduler = fil_scheduler_get(0);
    if (fil_scheduler == NULL)
    {
        Py_BEGIN_ALLOW_THREADS
#ifdef _POSIX_PRIORITY_SCHEDULING
        sched_yield();
#endif
        pthread_yield();
        Py_END_ALLOW_THREADS
        Py_RETURN_NONE;
    }

    current_gl = PyGreenlet_GetCurrent();
    if (current_gl == NULL)
        return NULL;

    fil_scheduler_gl_switch(fil_scheduler, NULL, current_gl);
    Py_DECREF(current_gl);
    fil_scheduler_switch(fil_scheduler);
    if (PyErr_Occurred())
        return NULL;
    Py_RETURN_NONE;
}

PyDoc_STRVAR(cext_spawn_doc, "Spawn a Filament.");
static PyFilament *cext_spawn(PyObject *_self, PyObject *args, PyObject *kwargs)
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
    {"sleep", (PyCFunction)cext_sleep, METH_VARARGS, cext_sleep_doc },
    {"spawn", (PyCFunction)cext_spawn, METH_VARARGS|METH_KEYWORDS, cext_spawn_doc },
    {"yield_thread", (PyCFunction)cext_yield, METH_NOARGS, cext_yield_doc },
    { NULL, NULL }
};

PyMODINIT_FUNC
init_filament(void)
{
    PyObject *m;

    PyGreenlet_Import();
    m = Py_InitModule3("_filament", cext_methods, cext_doc);
    if (m == NULL)
    {
        return;
    }
    filament_type_init(m);
    fil_lock_type_init(m);
    fil_cond_type_init(m);
    fil_message_type_init(m);
    fil_semaphore_type_init(m);
    fil_scheduler_type_init(m);
    fil_timer_type_init(m);
    fil_iothread_type_init(m);
    fil_io_init(m);
    fil_exceptions_init(m);
    fil_queue_module_init(m);
}

