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
#include <greenlet/greenlet.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include "filament.h"
#include "fil_scheduler.h"
#include "fil_message.h"
#include "fil_util.h"


typedef struct _pyfilament {
    PyGreenlet greenlet;
    PyFilScheduler *sched;
    PyFilMessage *message;
    PyObject *method;
    PyObject *method_args;
    PyObject *method_kwargs;
} PyFilament;


static PyObject *_filament_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    PyFilament *self = NULL;

    self = (PyFilament *)type->tp_alloc(type, 0);
    if (self == NULL)
        return NULL;
    return (PyObject *)self;
}

static int __filament_init(PyFilament *self, PyObject *method, PyObject *args, PyObject *kwargs)
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

    self->method = method;
    self->method_args = args;
    self->method_kwargs = kwargs;

    Py_INCREF(method);
    Py_INCREF(args);
    if (kwargs != NULL)
        Py_INCREF(kwargs);

    main_method = PyObject_GetAttrString((PyObject *)self, "main");
    if (main_method == NULL)
    {
        /* FIXME: raise */
        return -1;
    }

    self->sched = fil_scheduler_get(1);
    if (self->sched == NULL)
    {
        Py_DECREF(main_method);
        return -1;
    }

    /* XXX fil_scheduler_get() doesn't incref for us */
    Py_INCREF(self->sched);

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

static int _filament_init(PyFilament *self, PyObject *args, PyObject *kwargs)
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

    result = __filament_init(self, method, method_args, kwargs);
    Py_DECREF(method_args);
    return result;
}

static void _filament_dealloc(PyFilament *self)
{
    Py_CLEAR(self->message);
    Py_CLEAR(self->sched);
    Py_CLEAR(self->method);
    Py_CLEAR(self->method_args);
    Py_CLEAR(self->method_kwargs);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

PyDoc_STRVAR(filament_wait_doc, "Wait!");
static PyObject *_filament_wait(PyFilament *self, PyObject *args)
{
    return fil_message_wait(self->message, NULL);
}

PyDoc_STRVAR(filament_main_doc, "Main entrypoint for the Filament.");
static PyObject *_filament_main(PyFilament *self, PyObject *args)
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

static PyMethodDef _filament_methods[] = {
    {"wait", (PyCFunction)_filament_wait, METH_VARARGS, filament_wait_doc},
    {"main", (PyCFunction)_filament_main, METH_NOARGS, filament_main_doc},
    { NULL, NULL }
};

static PyTypeObject _filament_type = {
    PyVarObject_HEAD_INIT(0, 0)                 /* Must fill in type
                                                   value later */
    "filament.filament.Filament",               /* tp_name */
    sizeof(PyFilament),                         /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_filament_dealloc,              /* tp_dealloc */
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
    _filament_methods,                          /* tp_methods */
    0,
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_filament_init,                   /* tp_init */
    0,                                          /* tp_alloc */
    (newfunc)_filament_new,                     /* tp_new */
    0,                                          /* tp_free */
};


/****************/


int filament_type_init(PyObject *module)
{
    PyObject *m;

    PyGreenlet_Import();
    _filament_type.tp_base = &PyGreenlet_Type;
    if (PyType_Ready(&_filament_type) < 0)
        return -1;

    m = fil_create_module("filament.filament");
    if (m == NULL)
        return -1;

    Py_INCREF((PyObject *)&_filament_type);
    if (PyModule_AddObject(m, "Filament",
                           (PyObject *)&_filament_type) != 0)
    {
        Py_DECREF((PyObject *)&_filament_type);
        return -1;
    }

    if (PyModule_AddObject(module, "filament", m) != 0)
    {
        return -1;
    }

    Py_INCREF(m);

    return 0;
}

PyFilament *filament_alloc(PyObject *method, PyObject *args, PyObject *kwargs)
{
    PyFilament *self;

    self = (PyFilament *)_filament_new(&_filament_type, NULL, NULL);
    if (self == NULL)
        return NULL;
    if (__filament_init(self, method, args, kwargs) < 0)
    {
        Py_DECREF(self);
        return NULL;
    }
    return self;
}
