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

#define __FIL_BUILDING_CORE__
#include "core/filament.h"

typedef struct _pyfil_message {
    PyObject_HEAD

    FilWaiterList waiters;

    PyObject *result_or_exc_type;
    int is_exc;
    PyObject *exc_value; /* non NULL indicates exception */
    PyObject *exc_tb;
} PyFilMessage;


static PyFilMessage *_message_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    PyFilMessage *self = (PyFilMessage *)type->tp_alloc(type, 0);

    if (self != NULL) {
        fil_waiterlist_init(self->waiters);
    }

    return self;
}

static int _message_init(PyFilMessage *self, PyObject *args, PyObject *kargs)
{
    /* Returns -1 on error */
    return 0;
}

static void _message_dealloc(PyFilMessage *self)
{
    Py_CLEAR(self->result_or_exc_type);
    Py_CLEAR(self->exc_value);
    Py_CLEAR(self->exc_tb);
    assert(fil_waiterlist_empty(self->waiters));

    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *_message_result(PyFilMessage *self)
{
    Py_INCREF(self->result_or_exc_type);

    if (self->is_exc)
    {
        Py_XINCREF(self->exc_value);
        Py_XINCREF(self->exc_tb);
        PyErr_Restore(self->result_or_exc_type, self->exc_value,
                      self->exc_tb);
        return NULL;
    }

    return self->result_or_exc_type;
}

static PyObject *__message_wait(PyFilMessage *self, struct timespec *ts)
{
    int err;

    if (self->result_or_exc_type != NULL)
    {
        return _message_result(self);
    }

    err = fil_waiterlist_wait(self->waiters, ts);
    if (err)
    {
        return NULL;
    }

    return _message_result(self);
}

static int __message_send(PyFilMessage *self, PyObject *message)
{
    if (self->result_or_exc_type != NULL)
    {
        PyErr_SetString(PyExc_RuntimeError, "Can only send once");
        return -1;
    }

    Py_INCREF(message);
    self->result_or_exc_type = message;

    fil_waiterlist_signal_all(self->waiters);

    return 0;
}

static int __message_send_exception(PyFilMessage *self, PyObject *exc_type,
                                    PyObject *exc_value, PyObject *exc_tb)
{
    if (self->result_or_exc_type != NULL)
    {
        PyErr_SetString(PyExc_RuntimeError, "Can only send once");
        return -1;
    }

    self->is_exc = 1;
    Py_INCREF(exc_type);
    Py_XINCREF(exc_value);
    Py_XINCREF(exc_tb);

    self->result_or_exc_type = exc_type;
    self->exc_value = exc_value;
    self->exc_tb = exc_tb;

    fil_waiterlist_signal_all(self->waiters);

    return 0;
}

PyDoc_STRVAR(_message_wait_doc, "Wait!");
static PyObject *_message_wait(PyFilMessage *self, PyObject *args, PyObject *kwargs)
{
    /*
    struct timespec tsbuf;
    */
    struct timespec tsbuf;
    struct timespec *ts;
    PyObject *timeout = NULL;
    static char *keywords[] = {"timeout", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O:wait", keywords, &timeout))
    {
        return NULL;
    }

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    return __message_wait(self, ts);
}


PyDoc_STRVAR(_message_send_doc, "Send an object.");
static PyObject *_message_send(PyFilMessage *self, PyObject *message)
{
    if (__message_send(self, message) < 0)
    {
        return NULL;
    }
    Py_RETURN_NONE;
}

PyDoc_STRVAR(_message_send_exc_doc, "Tell a message to raise an exception.");
static PyObject *_message_send_exception(PyFilMessage *self, PyObject *args)
{
    PyObject *exc_type;
    PyObject *exc_value;
    PyObject *exc_tb;

    if (!PyArg_ParseTuple(args, "OOO:send_exception", &exc_type, &exc_value, &exc_tb))
        return NULL;

    if (__message_send_exception(self, exc_type, exc_value, exc_tb) < 0)
        return NULL;

    Py_RETURN_NONE;
}

static PyMethodDef _message_methods[] = {
    {"wait", (PyCFunction)_message_wait, METH_VARARGS|METH_KEYWORDS, _message_wait_doc},
    {"send", (PyCFunction)_message_send, METH_O, _message_send_doc},
    {"send_exception", (PyCFunction)_message_send_exception, METH_VARARGS, _message_send_exc_doc},
    { NULL, NULL }
};

static PyTypeObject _message_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "filament.message.Message",                 /* tp_name */
    sizeof(PyFilMessage),                       /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_message_dealloc,               /* tp_dealloc */
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
    _message_methods,                           /* tp_methods */
    0,
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_message_init,                    /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_message_new,                      /* tp_new */
    PyObject_Del,                               /* tp_free */
};


/****************/

PyFilMessage *fil_message_alloc(void)
{
    PyFilMessage *self;
    
    self = (PyFilMessage *)_message_new(&_message_type, NULL, NULL);
    if (self == NULL)
        return NULL;

    if (_message_init(self, NULL, NULL) < 0)
    {
        Py_DECREF(self);
        return NULL;
    }
    return self;
}

int fil_message_send(PyFilMessage *message, PyObject *result)
{
    return __message_send(message, result);
}

int fil_message_send_exception(PyFilMessage *message, PyObject *exc_type,
                               PyObject *exc_value, PyObject *exc_tb)
{
    return __message_send_exception(message, exc_type, exc_value, exc_tb);
}

PyObject *fil_message_wait(PyFilMessage *message, struct timespec *ts)
{
    return __message_wait(message, ts);
}

int fil_message_init(PyObject *module, PyFilCore_CAPIObject *capi)
{
    PyGreenlet_Import();
    if (PyType_Ready(&_message_type) < 0)
    {
        return -1;
    }

    Py_INCREF((PyObject *)&_message_type);
    if (PyModule_AddObject(module, "Message", (PyObject *)&_message_type) != 0)
    {
        Py_DECREF((PyObject *)&_message_type);
        return -1;
    }

    return 0;
}

