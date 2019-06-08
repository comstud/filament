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
#include <structmember.h>
#include <longintrepr.h>

#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
#include <sys/types.h>
#include <time.h>
#include "fil_iothread.h"
#include "fil_exceptions.h"
#include "fil_util.h"


#ifdef EWOULDBLOCK
#define WOULDBLOCK_ERRNO(__x) (((__x) == EAGAIN) || ((__x) == EWOULDBLOCK))
#else
#define WOULDBLOCK_ERRNO(__x) ((__x) == EAGAIN)
#endif


typedef struct _pyfil_fdesc
{
    PyLongObject long_obj;
    PyObject *sock;
} PyFilFDesc;

static PyMemberDef _fdesc_members[] = {
    {"_fil_sock", T_OBJECT, offsetof(PyFilFDesc, sock), 0},
    { NULL }
};

static PyTypeObject _fdesc_type = {
    PyVarObject_HEAD_INIT(0, 0)                 /* Must fill in type value later */
    "filament.io.FDesc",                        /* tp_name */
    sizeof(PyFilFDesc),                         /* tp_basicsize */
    0,                                          /* tp_itemsize */
    0,                                          /* tp_dealloc */
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
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,   /* tp_flags */
    0,                                          /* tp_doc */
    0,                                          /* tp_traverse */
    0,                                          /* tp_clear */
    0,                                          /* tp_richcompare */
    0,                                          /* tp_weaklistoffset */
    0,                                          /* tp_iter */
    0,                                          /* tp_iternext */
    0,                                          /* tp_methods */
    _fdesc_members,                             /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    0,                                          /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    0,                                          /* tp_new */
    PyObject_Del,                               /* tp_free */
};

static int _act_nonblock(PyObject *fd_obj)
{
    int act_nonblock = 1;
    PyObject *sock;
    PyObject *result;

    if ((Py_TYPE(fd_obj) == &_fdesc_type) &&
            ((sock = ((PyFilFDesc *)fd_obj)->sock) != NULL))
    {
        result = PyObject_GetAttrString(sock, "_act_nonblocking");

        if ((result != NULL) && PyBool_Check(result))
        {
            act_nonblock = (result == Py_True);
        }
        else
        {
            PyErr_Clear();
        }

        Py_XDECREF(result);
    }

    return act_nonblock;
}

PyDoc_STRVAR(_os_read_doc, "os.read() compatible method.");
static PyObject *_os_read(PyObject *self, PyObject *args)
{
    int fd;
    ssize_t result;
    size_t size;
    PyFilIOThread *iothr;

    PyObject *buffer;
    if (!PyArg_ParseTuple(args, "ii:read", &fd, &size))
        return NULL;
    if (size < 0) {
        errno = EINVAL;
        return PyErr_SetFromErrno(PyExc_OSError);
    }

    buffer = PyString_FromStringAndSize((char *)NULL, size);
    if (buffer == NULL)
        return NULL;

    if (_act_nonblock(PyTuple_GET_ITEM(args, 0)))
    {
        Py_BEGIN_ALLOW_THREADS;

        result = read(fd, PyString_AsString(buffer), size);

        Py_END_ALLOW_THREADS;
    }
    else
    {
        iothr = fil_iothread_get();
        result = fil_iothread_read(iothr, fd, PyString_AsString(buffer), size, NULL);
    }

    if (result < 0)
    {
        Py_DECREF(buffer);
        if (PyErr_Occurred())
            return NULL;
        return PyErr_SetFromErrno(PyExc_OSError);
    }

    if (result != size)
        _PyString_Resize(&buffer, result);
    return buffer;
}

PyDoc_STRVAR(_os_write_doc, "os.write() compatible method.");
static PyObject *_os_write(PyObject *self, PyObject *args)
{
    int fd;
    ssize_t result;
    ssize_t len;
    PyFilIOThread *iothr;
    Py_buffer pbuf;

    if (!PyArg_ParseTuple(args, "is*:write", &fd, &pbuf))
        return NULL;

    len = pbuf.len;

    if (_act_nonblock(PyTuple_GET_ITEM(args, 0)))
    {
        Py_BEGIN_ALLOW_THREADS;

        result = write(fd, pbuf.buf, len);

        Py_END_ALLOW_THREADS;
    }
    else
    {
        iothr = fil_iothread_get();
        result = fil_iothread_write(iothr, fd, pbuf.buf, len, NULL);
    }

    PyBuffer_Release(&pbuf);
    if (result < 0)
    {
        if (PyErr_Occurred())
            return NULL;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    return PyInt_FromSsize_t(result);
}

PyDoc_STRVAR(_fd_wait_read_ready_doc, "wait for fd to be ready for read.");
static PyObject *_fd_wait_read_ready(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"fd", "timeout", NULL};
    PyObject *timeout = NULL;
    struct timespec tsbuf;
    struct timespec *ts;
    int fd;
    int err;
    PyFilIOThread *iothr;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "i|O",
                                     keywords,
                                     &fd,
                                     &timeout))
    {
        return NULL;
    }

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    if (_act_nonblock(PyTuple_GET_ITEM(args, 0)))
    {
        Py_RETURN_NONE;
    }

    iothr = fil_iothread_get();

    err = fil_iothread_read_ready(iothr, fd, ts);
    if (err)
    {
        return NULL;
    }

    Py_RETURN_NONE;
}

PyDoc_STRVAR(_fd_wait_write_ready_doc, "wait for fd to be ready for write.");
static PyObject *_fd_wait_write_ready(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"fd", "timeout", NULL};
    PyObject *timeout = NULL;
    struct timespec tsbuf;
    struct timespec *ts;
    int fd;
    int err;
    PyFilIOThread *iothr;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "i|O",
                                     keywords,
                                     &fd,
                                     &timeout))
    {
        return NULL;
    }

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    if (_act_nonblock(PyTuple_GET_ITEM(args, 0)))
    {
        Py_RETURN_NONE;
    }

    iothr = fil_iothread_get();

    err = fil_iothread_write_ready(iothr, fd, ts);
    if (err)
    {
        return NULL;
    }

    Py_RETURN_NONE;
}

int fil_io_init(PyObject *module)
{
    PyObject *m;
    /* This needs to not fall out of scope */
    static PyMethodDef mds[] = {
        {"os_read", (PyCFunction)_os_read, METH_VARARGS, _os_read_doc},
        {"os_write", (PyCFunction)_os_write, METH_VARARGS, _os_write_doc},
        {"fd_wait_read_ready", (PyCFunction)_fd_wait_read_ready, METH_VARARGS|METH_KEYWORDS, _fd_wait_read_ready_doc},
        {"fd_wait_write_ready", (PyCFunction)_fd_wait_write_ready, METH_VARARGS|METH_KEYWORDS, _fd_wait_write_ready_doc},
        { NULL }
    };

    _fdesc_type.tp_base = &PyLong_Type;
    if (PyType_Ready(&_fdesc_type) < 0)
        return -1;

    m = fil_create_module("filament.io");
    if (m == NULL)
        return -1;

    Py_INCREF((PyObject *)&_fdesc_type);
    if (PyModule_AddObject(m, "FDesc",
                           (PyObject *)&_fdesc_type) != 0)
    {
        Py_DECREF((PyObject *)&_fdesc_type);
        Py_DECREF(m);
        return -1;
    }

    PyObject *n = PyString_FromString("filament.io");
    PyMethodDef *md;
    PyObject *cf;

    for(md=mds;md->ml_name;md++)
    {
        cf = PyCFunction_NewEx(md, (PyObject*)NULL, n);
        /* steals reference to 'cf' */
        PyModule_AddObject(m, md->ml_name, cf);
    }

    Py_DECREF(n);

    /* steals reference to 'm' */
    if (PyModule_AddObject(module, "io", m) != 0)
    {
        Py_DECREF((PyObject *)&_fdesc_type);
        Py_DECREF(m);
        return -1;
    }

    return 0;
}
