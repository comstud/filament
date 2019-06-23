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
    PyFilament *self = NULL;

    self = (PyFilament *)type->tp_alloc(type, 0);
    if (self == NULL)
        return NULL;
    return (PyObject *)self;
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

    fil_scheduler_gl_switch(self->sched, NULL, (PyGreenlet *)self);

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

static void _fil_filament_dealloc(PyFilament *self)
{
    Py_CLEAR(self->message);
    Py_CLEAR(self->sched);
    Py_CLEAR(self->method);
    Py_CLEAR(self->method_args);
    Py_CLEAR(self->method_kwargs);

    /* we inherit from greenlet which may or may not be
     * compiled with USE_GC, so we free via tp_free rather
     * than PyObject_(GC_)Del
     */
    Py_TYPE(self)->tp_free(self);
}

PyDoc_STRVAR(_fil_filament_wait_doc, "Wait!");
static PyObject *_fil_filament_wait(PyFilament *self, PyObject *args)
{
    return fil_message_wait(self->message, NULL);
}

PyDoc_STRVAR(_fil_filament_main_doc, "Main entrypoint for the Filament.");
static PyObject *_fil_filament_main(PyFilament *self, PyObject *args)
{
    PyObject *result = PyObject_Call(self->method, self->method_args,
                                     self->method_kwargs);
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

        fil_message_send_exception(self->message, exc_type, exc_value, exc_tb);
        /* Restore this so the scheduler can catch and force the main
         * greenlet to raise if they are system exceptions.
         */
        PyErr_Restore(exc_type, exc_value, exc_tb);
        return NULL;
    }
    else
    {
        fil_message_send(self->message, result);
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
    FIL_DEFAULT_TPFLAGS,                        /* tp_flags */
    0,                                          /* tp_doc */
    0,                                          /* tp_traverse */
    0,                                          /* tp_clear */
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

PyDoc_STRVAR(_fil_sleep_doc, "Sleep!");
static PyObject *_fil_sleep(PyObject *_self, PyObject *args)
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
    {
        Py_DECREF(fil_scheduler);
        return NULL;
    }

    fil_scheduler_gl_switch(fil_scheduler, ts, current_gl);
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
#endif
        pthread_yield();
        Py_END_ALLOW_THREADS
        Py_RETURN_NONE;
    }

    current_gl = PyGreenlet_GetCurrent();
    if (current_gl == NULL)
    {
        Py_DECREF(fil_scheduler);
        return NULL;
    }

    fil_scheduler_gl_switch(fil_scheduler, NULL, current_gl);
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
    {"sleep", (PyCFunction)_fil_sleep, METH_VARARGS, _fil_sleep_doc },
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

PyMODINIT_FUNC
initcore(void)
{
    PyObject *m;
    PyObject *capsule;

    PyGreenlet_Import();
    m = Py_InitModule3(FILAMENT_CORE_MODULE_NAME, cext_methods, cext_doc);
    if (m == NULL)
    {
        return;
    }

    _fil_filament_type.tp_base = &PyGreenlet_Type;
    if (PyType_Ready(&_fil_filament_type) < 0)
    {
        return;
    }

    Py_INCREF((PyObject *)&_fil_filament_type);
    if (PyModule_AddObject(m, "Filament",
                           (PyObject *)&_fil_filament_type) != 0)
    {
        Py_DECREF((PyObject *)&_fil_filament_type);
        return;
    }

    PyFilament_Type = &_fil_filament_type;
    _PY_FIL_CORE_API->filament_type = PyFilament_Type;
    _PY_FIL_CORE_API->filament_alloc = filament_alloc;

    if (fil_message_init(m, _PY_FIL_CORE_API) < 0 ||
        fil_scheduler_init(m, _PY_FIL_CORE_API) < 0 ||
        fil_exceptions_init(m, _PY_FIL_CORE_API) < 0)
    {
        return;
    }

    capsule = PyCapsule_New(_PY_FIL_CORE_API, FILAMENT_CORE_CAPSULE_NAME, NULL);
    if (PyModule_AddObject(m, FILAMENT_CORE_CAPI_NAME, capsule) != 0)
    {
        return;
    }

    return;
}

