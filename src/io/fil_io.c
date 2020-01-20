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

#define __FIL_BUILDING_IO__
#include "core/filament.h"
#include "io/fil_io.h"

#ifdef EWOULDBLOCK
#define WOULDBLOCK_ERRNO(__x) (((__x) == EAGAIN) || ((__x) == EWOULDBLOCK))
#else
#define WOULDBLOCK_ERRNO(__x) ((__x) == EAGAIN)
#endif

static PyFilIO_CAPIObject _PY_FIL_IO_API_STORAGE;
PyFilIO_CAPIObject *_PY_FIL_IO_API = &_PY_FIL_IO_API_STORAGE;

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
    "_filament.io.FDesc",                       /* tp_name */
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
    FIL_DEFAULT_TPFLAGS|Py_TPFLAGS_LONG_SUBCLASS, /* tp_flags */
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
    0,                                          /* tp_is_gc */
    0,                                          /* tp_bases */
    0,                                          /* tp_mro */
    0,                                          /* tp_cache */
    0,                                          /* tp_subclasses */
    0,                                          /* tp_weaklist */
    0,                                          /* tp_del */
    0,                                          /* tp_version_tag */
};

static inline int _act_nonblock(PyObject *fd_obj)
{
    return 0;
}

PyDoc_STRVAR(_os_read_doc, "os.read() compatible method.");
static PyObject *_os_read(PyObject *self, PyObject *args)
{
    int fd;
    ssize_t result = -1;
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
        if ((iothr = fil_iothread_get()) != NULL)
        {
            result = fil_iothread_read(iothr, fd, PyString_AsString(buffer), size, NULL, NULL);
            Py_DECREF(iothr);
        }
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
    ssize_t result = -1;
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
        if ((iothr = fil_iothread_get()) != NULL)
        {
            result = fil_iothread_write(iothr, fd, pbuf.buf, len, NULL, NULL);
            Py_DECREF(iothr);
        }
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

PyDoc_STRVAR(_abstimeout_from_timeout_doc, "return a tuple of (sec, nsec) suitable for abstimeout= arguments");
static PyObject *_abstimeout_from_timeout(PyObject *self, PyObject *arg)
{
    struct timespec tsbuf, *ts;
    PyObject *res, *sec, *nsec;

    if (fil_timespec_from_pyobj_interval(arg, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    if (ts == NULL)
    {
        Py_RETURN_NONE;
    }

    res = PyTuple_New(2);
    if (res == NULL)
    {
        return NULL;
    }

    sec = PyLong_FromLong(ts->tv_sec);
    if (sec == NULL)
    {
        Py_DECREF(res);
        return NULL;
    }

    nsec = PyLong_FromLong(ts->tv_nsec);
    if (nsec == NULL)
    {
        Py_DECREF(sec);
        Py_DECREF(res);
        return NULL;
    }

    if (PyTuple_SET_ITEM(res, 0, sec) < 0)
    {
        Py_DECREF(nsec);
        Py_DECREF(res);
        return NULL;
    }

    if (PyTuple_SET_ITEM(res, 1, nsec) < 0)
    {
        Py_DECREF(res);
        return NULL;
    }

    return res;
}

static int _timespec_from_abstimeout(PyObject *abstimeout, struct timespec *tsbuf, struct timespec **ts)
{
    long sec = -1, nsec = -1;

    if (abstimeout == NULL || abstimeout == Py_None)
    {
        /* leave untouched */
        return 1;
    }

    if (!PyTuple_Check(abstimeout) || PyTuple_GET_SIZE(abstimeout) != 2)
    {
        PyErr_SetString(PyExc_TypeError, "expected a tuple returned from abstimeout_from_timeout()");
        return -1;
    }

    if ((sec = PyLong_AsLong(PyTuple_GET_ITEM(abstimeout, 0))) < 0 && PyErr_Occurred())
    {
        return -1;
    }

    if ((nsec = PyLong_AsLong(PyTuple_GET_ITEM(abstimeout, 1))) < 0 && PyErr_Occurred())
    {
        return -1;
    }

    if (sec < 0 || nsec < 0)
    {
        PyErr_SetString(PyExc_TypeError, "expected a tuple returned from abstimeout_from_timeout()");
        return -1;
    }

    tsbuf->tv_sec = sec;
    tsbuf->tv_nsec = nsec;
    *ts = tsbuf;

    return 0;
}

PyDoc_STRVAR(_fd_wait_read_ready_doc, "wait for fd to be ready for read.");
static PyObject *_fd_wait_read_ready(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"fd", "timeout", "abstimeout", "timeout_exc", NULL};
    PyObject *timeout = NULL, *abstimeout = NULL, *timeout_exc = NULL;
    struct timespec tsbuf, *ts;
    int fd, err;
    PyFilIOThread *iothr;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "i|OOO:fd_wait_read_ready",
                                     keywords,
                                     &fd,
                                     &timeout,
                                     &abstimeout,
                                     &timeout_exc))
    {
        return NULL;
    }

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    /* takes precedence */
    if (_timespec_from_abstimeout(abstimeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    if ((iothr = fil_iothread_get()) == NULL)
    {
        return NULL;
    }

    err = fil_iothread_read_ready(iothr, fd, ts, timeout_exc);

    Py_DECREF(iothr);

    if (err)
    {
        return NULL;
    }

    Py_RETURN_NONE;
}

PyDoc_STRVAR(_fd_wait_write_ready_doc, "wait for fd to be ready for write.");
static PyObject *_fd_wait_write_ready(PyObject *self, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"fd", "timeout", "abstimeout", "timeout_exc", NULL};
    PyObject *timeout = NULL, *abstimeout = NULL, *timeout_exc = NULL;
    struct timespec tsbuf, *ts;
    int fd, err;
    PyFilIOThread *iothr;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "i|OOO:fd_wait_write_ready",
                                     keywords,
                                     &fd,
                                     &timeout,
                                     &abstimeout,
                                     &timeout_exc))
    {
        return NULL;
    }

    if (fil_timespec_from_pyobj_interval(timeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    /* takes precedence */
    if (_timespec_from_abstimeout(abstimeout, &tsbuf, &ts) < 0)
    {
        return NULL;
    }

    if ((iothr = fil_iothread_get()) == NULL)
    {
        return NULL;
    }

    err = fil_iothread_write_ready(iothr, fd, ts, timeout_exc);

    Py_DECREF(iothr);

    if (err)
    {
        return NULL;
    }

    Py_RETURN_NONE;
}

PyDoc_STRVAR(_fil_io_module_doc, "Filament _filament.io module");
static PyMethodDef _fil_io_module_methods[] = {
    { "os_read", (PyCFunction)_os_read, METH_VARARGS, _os_read_doc},
    { "os_write", (PyCFunction)_os_write, METH_VARARGS, _os_write_doc},
    { "fd_wait_read_ready", (PyCFunction)_fd_wait_read_ready, METH_VARARGS|METH_KEYWORDS, _fd_wait_read_ready_doc},
    { "fd_wait_write_ready", (PyCFunction)_fd_wait_write_ready, METH_VARARGS|METH_KEYWORDS, _fd_wait_write_ready_doc},
    { "abstimeout_from_timeout", (PyCFunction)_abstimeout_from_timeout, METH_O, _abstimeout_from_timeout_doc},
    { NULL }
};

_FIL_MODULE_INIT_FN_NAME(io)
{
    PyObject *m, *capsule;

    PyFilCore_Import();

    _FIL_MODULE_SET(m, FILAMENT_IO_MODULE_NAME, _fil_io_module_methods, _fil_io_module_doc);
    if (m == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    _fdesc_type.tp_base = &PyLong_Type;
    if (PyType_Ready(&_fdesc_type) < 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    Py_INCREF((PyObject *)&_fdesc_type);
    if (PyModule_AddObject(m, "FDesc",
                           (PyObject *)&_fdesc_type) != 0)
    {
        Py_DECREF((PyObject *)&_fdesc_type);
        return _FIL_MODULE_INIT_ERROR;
    }

    if (fil_iothread_init(m) < 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    capsule = PyCapsule_New(_PY_FIL_IO_API, FILAMENT_IO_CAPSULE_NAME, NULL);
    if (PyModule_AddObject(m, FILAMENT_IO_CAPI_NAME, capsule) != 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    return _FIL_MODULE_INIT_SUCCESS(m);
}
