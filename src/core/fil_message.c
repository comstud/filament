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

/*
 * Mutual exclusion for the message's state.
 *
 * None on a normal build, and none needed: send() and wait() both run with the
 * GIL held and neither releases it between testing 'result_or_exc_type' and
 * acting on that test.
 *
 * On a FREE-THREADING build (PEP 703) that is exactly the hole.  A Message is
 * how a REAL OS THREAD hands a result back to a parked greenthread -- it is
 * what filament.tpool is built on -- so the two sides genuinely run at once,
 * and the sequence
 *
 *   waiter:  test result == NULL          (not ready, so I will wait)
 *   sender:                               set result; signal_all()
 *   waiter:  add myself to the list; park
 *
 * loses the wakeup completely: the signal swept an empty list and the waiter
 * parks forever with the result already sitting there.  That is the
 * test_cross_thread_137 deadlock.  The lock makes test-and-add atomic against
 * set-and-signal; it is dropped for the park itself, inside
 * fil_waiterlist_wait_locked().
 *
 * Lock order is msg_lock -> waiter_lock -> sched_lock, the same direction the
 * io layer and the queue use.
 */
#ifdef Py_GIL_DISABLED
#  define FIL_MSG_LOCK(__m)    pthread_mutex_lock(&((__m)->lock))
#  define FIL_MSG_UNLOCK(__m)  pthread_mutex_unlock(&((__m)->lock))
#  define FIL_MSG_LOCKP(__m)   (&((__m)->lock))
#else
#  define FIL_MSG_LOCK(__m)    ((void)0)
#  define FIL_MSG_UNLOCK(__m)  ((void)0)
#  define FIL_MSG_LOCKP(__m)   NULL
#endif

typedef struct _pyfil_message {
    PyObject_HEAD

    FilWaiterList waiters;

    PyObject *result_or_exc_type;
    int is_exc;
    PyObject *exc_value; /* non NULL indicates exception */
    PyObject *exc_tb;
#ifdef Py_GIL_DISABLED
    pthread_mutex_t lock;
#endif
} PyFilMessage;


static PyFilMessage *_message_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    PyFilMessage *self = (PyFilMessage *)type->tp_alloc(type, 0);

    if (self != NULL) {
        fil_waiterlist_init(self->waiters);
#ifdef Py_GIL_DISABLED
        pthread_mutex_init(&(self->lock), NULL);
#endif
    }

    return self;
}

static int _message_init(PyFilMessage *self, PyObject *args, PyObject *kargs)
{
    /* Returns -1 on error */
    return 0;
}

/*
 * GC support.
 *
 * A Message holds whatever the greenthread produced -- its return value, or
 * its exception plus traceback -- and every Filament owns one. Both payloads
 * routinely close a cycle back to the Message's owner:
 *
 *   Filament -> message -> exc_tb -> frame -> the object whose bound method
 *   was the greenthread body -> that object's back-reference to the Filament
 *
 * That is not exotic; it is what happens every time a greenthread running a
 * bound method raises (including the GreenletExit thrown by kill()). Without
 * a traverse the collector cannot see through the Message, so the whole
 * cluster -- exception, traceback, frames and all the locals they pin --
 * leaks. The waiter list needs no traversal: waiters are plain C structs that
 * only exist while a greenthread is parked, and a parked greenthread is not
 * collectable anyway.
 */
static int _message_traverse(PyFilMessage *self, visitproc visit, void *arg)
{
    Py_VISIT(self->result_or_exc_type);
    Py_VISIT(self->exc_value);
    Py_VISIT(self->exc_tb);
    return 0;
}

static int _message_clear(PyFilMessage *self)
{
    Py_CLEAR(self->result_or_exc_type);
    Py_CLEAR(self->exc_value);
    Py_CLEAR(self->exc_tb);
    return 0;
}

static void _message_dealloc(PyFilMessage *self)
{
    PyObject_GC_UnTrack(self);

    Py_CLEAR(self->result_or_exc_type);
    Py_CLEAR(self->exc_value);
    Py_CLEAR(self->exc_tb);
    assert(fil_waiterlist_empty(self->waiters));
#ifdef Py_GIL_DISABLED
    pthread_mutex_destroy(&(self->lock));
#endif

    /* Respect tp_free: Python subclass instances are GC-allocated, and
     * PyObject_Del on them frees the wrong pointer (heap corruption). */
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

#ifndef Py_GIL_DISABLED

/* Stock build: verbatim.  The GIL is the mutual exclusion. */
static PyObject *__message_wait(PyFilMessage *self, struct timespec *ts)
{
    int err;

    if (self->result_or_exc_type != NULL)
    {
        return _message_result(self);
    }

    err = fil_waiterlist_wait(self->waiters, ts, NULL);
    if (err)
    {
        return NULL;
    }

    return _message_result(self);
}

#else  /* Py_GIL_DISABLED */

static PyObject *__message_wait(PyFilMessage *self, struct timespec *ts)
{
    PyObject *res;
    int err;

    FIL_MSG_LOCK(self);

    if (self->result_or_exc_type != NULL)
    {
        res = _message_result(self);
        FIL_MSG_UNLOCK(self);
        return res;
    }

    err = fil_waiterlist_wait_locked(self->waiters, ts, NULL,
                                     FIL_MSG_LOCKP(self));
    if (err)
    {
        FIL_MSG_UNLOCK(self);
        return NULL;
    }

    res = _message_result(self);
    FIL_MSG_UNLOCK(self);
    return res;
}

#endif /* Py_GIL_DISABLED */

static int __message_send(PyFilMessage *self, PyObject *message)
{
    FIL_MSG_LOCK(self);

    if (self->result_or_exc_type != NULL)
    {
        FIL_MSG_UNLOCK(self);
        PyErr_SetString(PyExc_RuntimeError, "Can only send once");
        return -1;
    }

    Py_INCREF(message);
    self->result_or_exc_type = message;

    fil_waiterlist_signal_all(self->waiters);
    FIL_MSG_UNLOCK(self);

    return 0;
}

static int __message_send_exception(PyFilMessage *self, PyObject *exc_type,
                                    PyObject *exc_value, PyObject *exc_tb)
{
    FIL_MSG_LOCK(self);

    if (self->result_or_exc_type != NULL)
    {
        FIL_MSG_UNLOCK(self);
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
    FIL_MSG_UNLOCK(self);

    return 0;
}

PyDoc_STRVAR(_message_wait_doc, "Wait!");
static PyObject *_message_wait_common(PyFilMessage *self, PyObject *timeout)
{
    struct timespec tsbuf;
    struct timespec *ts;

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    return __message_wait(self, ts);
}

#ifdef _FIL_PYTHON3
static PyObject *_message_wait(PyFilMessage *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    static const char * const keywords[] = {"timeout"};
    PyObject *argv[1];

    if (fil_fastcall_parse(args, nargs, kwnames, "wait",
                           0, 1, keywords, argv) < 0)
    {
        return NULL;
    }

    return _message_wait_common(self, argv[0]);
}
#else
static PyObject *_message_wait(PyFilMessage *self, PyObject *args, PyObject *kwargs)
{
    PyObject *timeout = NULL;
    static char *keywords[] = {"timeout", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O:wait", keywords, &timeout))
    {
        return NULL;
    }

    return _message_wait_common(self, timeout);
}
#endif


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
#ifdef _FIL_PYTHON3
    {"wait", (PyCFunction)(void (*)(void))_message_wait, METH_FASTCALL|METH_KEYWORDS, _message_wait_doc},
#else
    {"wait", (PyCFunction)_message_wait, METH_VARARGS|METH_KEYWORDS, _message_wait_doc},
#endif
    {"send", (PyCFunction)_message_send, METH_O, _message_send_doc},
    {"send_exception", (PyCFunction)_message_send_exception, METH_VARARGS, _message_send_exc_doc},
    { NULL, NULL }
};

static PyTypeObject _message_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "_filament.Message",                        /* tp_name */
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
    FIL_DEFAULT_TPFLAGS|Py_TPFLAGS_HAVE_GC,     /* tp_flags */
    0,                                          /* tp_doc */
    (traverseproc)_message_traverse,            /* tp_traverse */
    (inquiry)_message_clear,                    /* tp_clear */
    0,                                          /* tp_richcompare */
    0,                                          /* tp_weaklistoffset */
    0,                                          /* tp_iter */
    0,                                          /* tp_iternext */
    _message_methods,                           /* tp_methods */
    0,                                          /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_message_init,                    /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_message_new,                      /* tp_new */
    /* Must match tp_alloc: PyType_GenericAlloc allocates (and tracks) a GC
     * header for Py_TPFLAGS_HAVE_GC types, so freeing with PyObject_Del
     * would hand the allocator the wrong pointer. */
    PyObject_GC_Del,                            /* tp_free */
    0,                                          /* tp_is_gc */
    0,                                          /* tp_bases */
    0,                                          /* tp_mro */
    0,                                          /* tp_cache */
    0,                                          /* tp_subclasses */
    0,                                          /* tp_weaklist */
    0,                                          /* tp_del */
    0,                                          /* tp_version_tag */
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

