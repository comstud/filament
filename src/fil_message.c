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
#include <string.h>
#include <sys/time.h>
#include <pthread.h>
#include <greenlet/greenlet.h>
#include "fil_message.h"
#include "fil_waiter.h"
#include "fil_util.h"


typedef struct _waiterlist WaiterList;

typedef struct _waiterlist
{
    PyFilWaiter *waiter;
    WaiterList *prev;
    WaiterList *next;
} WaiterList;

typedef struct _pyfil_message {
    PyObject_HEAD
    uint32_t tot_waiters;
    WaiterList *waiters;
    WaiterList *last_waiter;
    PyObject *resufil_or_exc_type;
    PyObject *exc_value; /* non NULL indicates exception */
    PyObject *exc_tb;
} PyFilMessage;


static PyObject *_message_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    return type->tp_alloc(type, 0);
}

static int _message_init(PyFilMessage *self, PyObject *args, PyObject *kargs)
{
    /* Returns -1 on error */
    return 0;
}

static WaiterList *_waiter_add(PyFilMessage *message, PyFilWaiter *waiter)
{
    WaiterList *waiterlist = malloc(sizeof(WaiterList));

    if (waiterlist == NULL)
        return NULL;
    waiterlist->waiter = waiter;
    waiterlist->next = NULL;
    if ((waiterlist->prev = message->last_waiter) == NULL)
        message->waiters = waiterlist;
    else
        waiterlist->prev->next = waiterlist;
    message->last_waiter = waiterlist;
    return waiterlist;
}

static PyFilWaiter *_waiter_remove(PyFilMessage *message, WaiterList *waiterlist)
{
    PyFilWaiter *waiter = waiterlist->waiter;

    if (waiterlist->prev)
        waiterlist->prev->next = waiterlist->next;
    else
        message->waiters = waiterlist->next;
    if (waiterlist->next)
        waiterlist->next->prev = waiterlist->prev;
    else
        message->last_waiter = waiterlist->prev;
    free(waiterlist);
    return waiter;
}

static void _message_dealloc(PyFilMessage *self)
{
    Py_CLEAR(self->resufil_or_exc_type);
    Py_CLEAR(self->exc_value);
    Py_CLEAR(self->exc_tb);
    assert(self->tot_waiters == 0);
    assert(self->waiters == NULL);

    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *_message_result(PyFilMessage *self)
{
    Py_INCREF(self->resufil_or_exc_type);

    if (self->exc_value)
    {
        if (self->exc_value)
            Py_INCREF(self->exc_value);
        if (self->exc_tb)
            Py_INCREF(self->exc_tb);
        PyErr_Restore(self->resufil_or_exc_type, self->exc_value,
                      self->exc_tb);
        return NULL;
    }

    return self->resufil_or_exc_type;
}

static PyObject *__message_wait(PyFilMessage *self, struct timespec *ts)
{
    PyFilWaiter *waiter;
    WaiterList *waiterlist;
    int err;

    if (self->resufil_or_exc_type != NULL)
    {
        return _message_result(self);
    }

    if (self->tot_waiters == (uint32_t)-1)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "Maximum number of waiters reached");
        return NULL;
    }

    waiter = fil_waiter_alloc();
    if (waiter == NULL)
    {
        return NULL;
    }

    waiterlist = _waiter_add(self, waiter);
    if (waiterlist == NULL)
    {
        Py_DECREF(waiter);
        return PyErr_NoMemory();
    }

    self->tot_waiters++;
    err = fil_waiter_wait(waiter, ts);
    self->tot_waiters--;

    if (err)
    {
        _waiter_remove(self, waiterlist);
        Py_DECREF(waiter);
        return NULL;
    }

    /* waiter was removed from list when sending */
    Py_DECREF(waiter);

    return _message_result(self);
}

static int __message_send(PyFilMessage *self, PyObject *message)
{
    WaiterList *waiterlist;
    PyFilWaiter *waiter;

    if (self->resufil_or_exc_type != NULL)
    {
        PyErr_SetString(PyExc_RuntimeError, "Can only send once");
        return -1;
    }

    Py_INCREF(message);
    self->resufil_or_exc_type = message;

    while((waiterlist = self->waiters) != NULL)
    {
        waiter = waiterlist->waiter;
        _waiter_remove(self, waiterlist);
        fil_waiter_signal(waiter, 0);
    }

    return 0;
}

static int __message_send_exception(PyFilMessage *self, PyObject *exc_type,
                                    PyObject *exc_value, PyObject *exc_tb)
{
    WaiterList *waiterlist;
    PyFilWaiter *waiter;

    if (self->resufil_or_exc_type != NULL)
    {
        PyErr_SetString(PyExc_RuntimeError, "Can only send once");
        return -1;
    }

    Py_INCREF(exc_type);
    if (exc_value != NULL)
        Py_INCREF(exc_value);
    if (exc_tb != NULL)
        Py_INCREF(exc_tb);

    self->resufil_or_exc_type = exc_type;
    self->exc_value = exc_value;
    self->exc_tb = exc_tb;

    while((waiterlist = self->waiters) != NULL)
    {
        waiter = waiterlist->waiter;
        _waiter_remove(self, waiterlist);
        fil_waiter_signal(waiter, 0);
    }

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

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O", keywords, &timeout))
    {
        return NULL;
    }

    if (fil_timeoutobj_to_timespec(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    return __message_wait(self, ts);
}


PyDoc_STRVAR(_message_send_doc, "Send a message to a message.");
static PyObject *_message_send(PyFilMessage *self, PyObject *args)
{
    PyObject *message;

    if (!PyArg_ParseTuple(args, "O", &message))
        return NULL;

    if (__message_send(self, message) < 0)
        return NULL;

    Py_RETURN_NONE;
}

PyDoc_STRVAR(_message_send_exc_doc, "Tell a message to raise an exception.");
static PyObject *_message_send_exception(PyFilMessage *self, PyObject *args)
{
    PyObject *exc_type;
    PyObject *exc_value;
    PyObject *exc_tb;

    if (!PyArg_ParseTuple(args, "OOO", &exc_type, &exc_value, &exc_tb))
        return NULL;

    if (__message_send_exception(self, exc_type, exc_value, exc_tb) < 0)
        return NULL;

    Py_RETURN_NONE;
}

static PyMethodDef _message_methods[] = {
    {"wait", (PyCFunction)_message_wait, METH_VARARGS|METH_KEYWORDS, _message_wait_doc},
    {"send", (PyCFunction)_message_send, METH_VARARGS, _message_send_doc},
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

int fil_message_type_init(PyObject *module)
{
    PyObject *m;

    PyGreenlet_Import();
    if (PyType_Ready(&_message_type) < 0)
        return -1;

    m = fil_create_module("filament.message");
    if (m == NULL)
        return -1;

    Py_INCREF((PyObject *)&_message_type);
    if (PyModule_AddObject(m, "Message", (PyObject *)&_message_type) != 0)
    {
        Py_DECREF((PyObject *)&_message_type);
        return -1;
    }

    if (PyModule_AddObject(module, "message", m) != 0)
    {
        return -1;
    }

    Py_INCREF(m);

    return 0;
}

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
