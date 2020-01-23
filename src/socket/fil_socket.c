/*
 * The MIT License (MIT): http://opensource.org/licenses/mit-license.php
 *
 * Copyright (c) 2019, Chris Behrens
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

#define __FIL_BUILDING_SOCKET__
#include "core/filament.h"
#include "io/fil_io.h"
#include "socket/fil_socket.h"

#define FIL_SOCKET_MODULE_NAME "_filament.socket"
#define FIL_DEFAULT_RESOLVER_CLASS "filament.thrpool_resolver"

#ifndef INVALID_SOCKET
#define INVALID_SOCKET (-1)
#endif

#if SIZEOF_SOCKET_T <= SIZEOF_LONG
#define PyLong_FromSocket_t(fd) PyLong_FromLong((SOCKET_T)(fd))
#define PyLong_AsSocket_t(fd) (SOCKET_T)PyLong_AsLong(fd)
#else
#define PyLong_FromSocket_t(fd) PyLong_FromLongLong((SOCKET_T)(fd))
#define PyLong_AsSocket_t(fd) (SOCKET_T)PyLong_AsLongLong(fd)
#endif

#ifdef MS_WINDOWS
typedef SOCKET SOCKET_T;
#       ifdef MS_WIN64
#               define SIZEOF_SOCKET_T 8
#       else
#               define SIZEOF_SOCKET_T 4
#       endif
#else
typedef int SOCKET_T;
#       define SIZEOF_SOCKET_T SIZEOF_INT
#endif

#define FIL_CPROXY_NOARG(NAME)                                              \
static PyObject *_sock_ ## NAME(PyFilSocket *self)                          \
{                                                                           \
    if (self->_sock_ ## NAME == NULL)                                       \
    {                                                                       \
        if ((self->_sock_ ## NAME = PyObject_GetAttrString(self->_sock, #NAME)) == NULL) \
        {                                                                   \
            return NULL;                                                    \
        }                                                                   \
    }                                                                       \
    return PyObject_Call(self->_sock_ ## NAME, _EMPTY_TUPLE, NULL);         \
}

#define FIL_CPROXY_ARG(NAME)                                                \
static PyObject *_sock_ ## NAME(PyFilSocket *self, PyObject *arg)           \
{                                                                           \
    PyObject *res, *args;                                                   \
    if (self->_sock_ ## NAME == NULL)                                       \
    {                                                                       \
        if ((self->_sock_ ## NAME = PyObject_GetAttrString(self->_sock, #NAME)) == NULL) \
        {                                                                   \
            return NULL;                                                    \
        }                                                                   \
    }                                                                       \
    args = PyTuple_New(1);                                                  \
    Py_INCREF(arg);                                                         \
    PyTuple_SET_ITEM(args, 0, arg);                                         \
    res = PyObject_Call(self->_sock_ ## NAME, args, NULL);                  \
    Py_DECREF(args);                                                        \
    return res;                                                             \
}

#define FIL_CPROXY_VARG(NAME)                                               \
static PyObject *_sock_ ## NAME(PyFilSocket *self, PyObject *args)          \
{                                                                           \
    if (self->_sock_ ## NAME == NULL)                                       \
    {                                                                       \
        if ((self->_sock_ ## NAME = PyObject_GetAttrString(self->_sock, #NAME)) == NULL) \
        {                                                                   \
            return NULL;                                                    \
        }                                                                   \
    }                                                                       \
    return PyObject_Call(self->_sock_ ## NAME, args, NULL);                 \
}

#define FIL_PROXY_NOARG(NAME)                                               \
static PyObject *_sock_ ## NAME(PyFilSocket *self)                          \
{                                                                           \
    PyObject *attr, *res;                                                   \
    if ((attr = PyObject_GetAttrString(self->_sock, #NAME)) == NULL)        \
    {                                                                       \
        return NULL;                                                        \
    }                                                                       \
    res = PyObject_Call(attr, _EMPTY_TUPLE, NULL);                          \
    Py_DECREF(attr);                                                        \
    return res;                                                             \
}

#define FIL_PROXY_ARG(NAME)                                                 \
static PyObject *_sock_ ## NAME(PyFilSocket *self, PyObject *arg)           \
{                                                                           \
    PyObject *attr, *res, *args;                                            \
    if ((attr = PyObject_GetAttrString(self->_sock, #NAME)) == NULL)        \
    {                                                                       \
        return NULL;                                                        \
    }                                                                       \
    if ((args = PyTuple_New(1)) == NULL)                                    \
    {                                                                       \
        Py_DECREF(attr);                                                    \
        return NULL;                                                        \
    }                                                                       \
    Py_INCREF(arg);                                                         \
    PyTuple_SET_ITEM(args, 0, arg);                                         \
    res = PyObject_Call(attr, args, NULL);                                  \
    Py_DECREF(attr);                                                        \
    Py_DECREF(args);                                                        \
    return res;                                                             \
}

#define FIL_PROXY_VARG(NAME)                                                \
static PyObject *_sock_ ## NAME(PyFilSocket *self, PyObject *args)          \
{                                                                           \
    PyObject *attr, *res;                                                   \
    if ((attr = PyObject_GetAttrString(self->_sock, #NAME)) == NULL)        \
    {                                                                       \
        return NULL;                                                        \
    }                                                                       \
    res = PyObject_Call(attr, args, NULL);                                  \
    Py_DECREF(attr);                                                        \
    return res;                                                             \
}

#define FIL_CPROXY_POLL(NAME, SIG, CALLARG, READ_OR_WRITE)                  \
static PyObject *_sock_ ## NAME SIG                                         \
{                                                                           \
    PyObject *attr;                                                         \
    PyObject *res = NULL;                                                   \
    PyFilIOThread *iothr;                                                   \
    struct timespec ts_buf, *ts;                                            \
    int err;                                                                \
    if ((attr = self->_sock_ ## NAME) == NULL)                              \
    {                                                                       \
        attr = self->_sock_##NAME = PyObject_GetAttrString(                 \
                self->_sock, #NAME);                                        \
        if (attr == NULL)                                                   \
        {                                                                   \
            return NULL;                                                    \
        }                                                                   \
    }                                                                       \
    Py_INCREF(attr);                                                        \
    if (self->timeout == 0.0 ||                                             \
            self->flags & PYFIL_SOCKET_FLAGS_TRY_WITHOUT_POLL)              \
    {                                                                       \
        res = PyObject_Call CALLARG;                                        \
        if ((res != NULL) || (self->timeout == 0.0) || !_exc_is_eagain(1))  \
        {                                                                   \
            goto out;                                                       \
        }                                                                   \
        self->first_misses++;                                               \
    }                                                                       \
    if (fil_timespec_from_double_interval(self->timeout, &ts_buf, &ts))     \
    {                                                                       \
        goto out;                                                           \
    }                                                                       \
    if ((iothr = fil_iothread_get()) == NULL)                               \
    {                                                                       \
        goto out;                                                           \
    }                                                                       \
    do                                                                      \
    {                                                                       \
        if ((err = fil_iothread_ ## READ_OR_WRITE ## _ready(                \
                        iothr, self->_sock_fd, ts, _SOCK_TIMEOUT)))         \
        {                                                                   \
            break;                                                          \
        }                                                                   \
        res = PyObject_Call CALLARG;                                        \
    } while(res == NULL && _exc_is_eagain(1));                              \
    Py_DECREF(iothr);                                                       \
out:                                                                        \
    Py_DECREF(attr);                                                        \
    return res;                                                             \
}

typedef struct _pyfil_socket {
    PyObject_HEAD

    int family;
    int type;
    int proto;
    int first_misses;

#define PYFIL_SOCKET_FLAGS_TRY_WITHOUT_POLL 0x00000001
#define PYFIL_SOCKET_FLAGS_DO_IN_BACKGROUND 0x00000002
    int flags;

    PyObject *_sock; /* built-in _socket object */
    SOCKET_T _sock_fd;
#if _FIL_PYTHON3
    PyObject *_sock__accept;
#else
    PyObject *_sock_accept;
#endif
    PyObject *_sock_getpeername;
    PyObject *_sock_getsockname;
    PyObject *_sock_getsockopt;
#if defined(MS_WINDOWS) && defined(SIO_RCVALL)
    PyObject *_sock_ioctl;
#endif
    PyObject *_sock_recvfrom;
    PyObject *_sock_recvfrom_into;
    PyObject *_sock_sendto;
    double timeout;
} PyFilSocket;

static PyObject *_SOCK_MODULE, *_SOCK_CLASS, *_SOCK_ERROR, *_SOCK_HERROR, *_SOCK_TIMEOUT;
static PyObject *_SOCK_SOCKETPAIR;
static PyObject *_EMPTY_TUPLE;
static PyTypeObject *_PYFIL_SOCK_TYPE;

#if 0
static char *_PROXY_METHOD_NAMES[] = {
    "bind",
    "close",
    "dup",
    "getpeername",
    "getsockname",
    "getsockopt",
#if defined(MS_WINDOWS) && defined(SIO_RCVALL)
    "ioctl",
#endif
    "listen",
    "setsockopt",
    "shutdown",
    NULL,
};
#endif

static PyObject *_RESOLVER, *_RESOLVER_METHOD_LIST;
/* these get copied in when module is loaded */
static char *_RESOLVER_METHOD_NAMES[] = {
    "gethostbyname",
    "gethostbyname_ex",
    "gethostbyaddr",
    "getaddrinfo",
    "getnameinfo",
    NULL,
};
#define _NUM_RESOLVER_METHODS ((sizeof(_RESOLVER_METHOD_NAMES) / sizeof(char *)) - 1)
static PyObject *_RESOLVER_METHODS[_NUM_RESOLVER_METHODS];

static inline int _copy_resolver_methods(PyObject *resolver)
{
    PyObject *fn;
    char **meth_name_ptr = _RESOLVER_METHOD_NAMES;
    PyObject **meth_ptr = _RESOLVER_METHODS;

    for(; *meth_name_ptr; meth_name_ptr++, meth_ptr++)
    {
        fn = PyObject_GetAttrString(resolver, *meth_name_ptr);
        /* shouldn't fail as we verify they exist before _copy is called */
        if (fn == NULL)
        {
            return -1;
        }

        Py_XSETREF(*meth_ptr, fn);
    }

    return 0;
}

static inline void _clear_resolver_methods(void)
{
    int i;

    for(i = 0; i < _NUM_RESOLVER_METHODS; i++)
    {
        Py_CLEAR(_RESOLVER_METHODS[i]);
    }
}

static inline int _check_resolver_methods(PyObject *resolver)
{
    char **meth_name_ptr = _RESOLVER_METHOD_NAMES;

    for(; *meth_name_ptr ; meth_name_ptr++)
    {
        if (!PyObject_HasAttrString(resolver, *meth_name_ptr))
        {
            PyErr_Format(PyExc_TypeError, "resolver class is missing function '%s'", *meth_name_ptr);
            return -1;
        }
    }
    return 0;
}

#if _FIL_PYTHON3
static inline int _exc_is_blockingioerr(int clear_on_match)
{
    PyObject *exc_type, *exc_value, *exc_tb;

    PyErr_Fetch(&exc_type, &exc_value, &exc_tb);
    if (exc_type == NULL)
    {
        return 0;
    }

    if (exc_value == NULL || (exc_type != PyExc_BlockingIOError))
    {
        PyErr_Restore(exc_type, exc_value, exc_tb);
        return 0;
    }

    if (clear_on_match)
    {
        Py_XDECREF(exc_type);
        Py_XDECREF(exc_value);
        Py_XDECREF(exc_tb);
    }
    else
    {
        PyErr_Restore(exc_type, exc_value, exc_tb);
    }

    return 1;
}

static inline int _exc_is_inprogress(int clear_on_match)
{
    return _exc_is_blockingioerr(clear_on_match);
}

static inline int _exc_is_eagain(int clear_on_match)
{
    return _exc_is_blockingioerr(clear_on_match);
}
#else
static inline int _exc_socket_error_errno(int *err_ret)
{
    PyObject *exc_type, *exc_value, *exc_tb;

    PyErr_Fetch(&exc_type, &exc_value, &exc_tb);
    if (exc_type == NULL)
    {
        return 0;
    }

    if (exc_value == NULL ||
            !PyErr_GivenExceptionMatches(exc_type, _SOCK_ERROR) ||
            !PyTuple_Check(exc_value) ||
            PyTuple_GET_SIZE(exc_value) < 1)
    {
        PyErr_Restore(exc_type, exc_value, exc_tb);
        return 0;
    }

    *err_ret = (int)PyInt_AsLong(PyTuple_GET_ITEM(exc_value, 0));

    PyErr_Restore(exc_type, exc_value, exc_tb);
    return 1;
}

static inline int _exc_is_errno(int errn, int clear_on_match)
{
    int err;

    if (_exc_socket_error_errno(&err) && (err == errn))
    {
        if (clear_on_match)
        {
            PyErr_Clear();
        }
        return 1;
    }
    return 0;
}

static inline int _exc_is_inprogress(int clear_on_match)
{
    return _exc_is_errno(EINPROGRESS, clear_on_match);
}

static inline int _exc_is_eagain(int clear_on_match)
{
    int err;

    if (_exc_socket_error_errno(&err) && FIL_IS_EAGAIN(err))
    {
        if (clear_on_match)
        {
            PyErr_Clear();
        }
        return 1;
    }
    return 0;
}

#endif /* _FIL_PYTHON3 */

static inline int _fileno_from_obj(PyObject *fileno_obj, SOCKET_T *fileno_ret)
{
    /* NOTE: a duped fd might be some encoded thing in windows.
     * ignoring that for now
     */
#if SIZEOF_SOCKET_T <= SIZEOF_LONG
    *fileno_ret = (SOCKET_T)PyInt_AsLong(fileno_obj);
#else
    *fileno_ret = (SOCKET_T)PyLong_AsLongLong(fileno_obj);
#endif

    if (*fileno_ret == (SOCKET_T)-1 && PyErr_Occurred())
    {
        PyErr_SetString(PyExc_ValueError, "Received unexpected type for file descriptor");
        return -1;
    }

    if (*fileno_ret == INVALID_SOCKET)
    {
        PyErr_SetString(PyExc_ValueError, "invalid socket value");
        return -1;
    }

    return 0;
}

static inline int _fileno_from_internal_socket(PyObject *_sock, SOCKET_T *fileno_ret)
{
    PyObject *res;

    res = PyObject_CallMethod(_sock, "fileno", "");
    if (res == NULL)
    {
        return -1;
    }

    if (_fileno_from_obj(res, fileno_ret) < 0)
    {
        Py_DECREF(res);
        return -1;
    }

    Py_DECREF(res);

    return 0;
}


static void _sock_clear_methods(PyFilSocket *self)
{
#if _FIL_PYTHON3
    Py_CLEAR(self->_sock__accept);
#else
    Py_CLEAR(self->_sock_accept);
#endif
    Py_CLEAR(self->_sock_getpeername);
    Py_CLEAR(self->_sock_getsockname);
    Py_CLEAR(self->_sock_getsockopt);
#if defined(MS_WINDOWS) && defined(SIO_RCVALL)
    Py_CLEAR(self->_sock_ioctl);
#endif
    Py_CLEAR(self->_sock_recvfrom);
    Py_CLEAR(self->_sock_recvfrom_into);
    Py_CLEAR(self->_sock_sendto);
}

static PyFilSocket *_sock_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    PyFilSocket *self = (PyFilSocket *)type->tp_alloc(type, 0);
    if (self != NULL) {
        self->_sock_fd = INVALID_SOCKET;
        self->timeout = -1.0;
        self->flags = (PYFIL_SOCKET_FLAGS_DO_IN_BACKGROUND|PYFIL_SOCKET_FLAGS_TRY_WITHOUT_POLL);
    }
    return self;
}

static inline PyObject *_new_internal_socket(int family, int type, int proto, SOCKET_T fileno)
{
    PyObject *_sock;
    PyObject *_sock_args;

#if _FIL_PYTHON3
    if (fileno < 0)
#endif
    {
        _sock_args = Py_BuildValue("iii", family, type, proto);
    }
#if _FIL_PYTHON3
    else
    {
        _sock_args = Py_BuildValue("iiii", family, type, proto, fileno);
    }
#endif

    if (_sock_args == NULL)
    {
        return NULL;
    }

    _sock = PyObject_Call(_SOCK_CLASS, _sock_args, NULL);
    Py_DECREF(_sock_args);

    return _sock;
}

static inline int _sock_init_from_sock_and_fileno(PyFilSocket *self, PyObject *_sock, SOCKET_T fileno)
{
    double timeout = -1.0;
    PyObject *res;

    res = PyObject_CallMethod(_sock, "gettimeout", "");
    if (res == NULL)
    {
        return -1;
    }

    if (res != Py_None)
    {
        timeout = PyFloat_AsDouble(res);
        if (PyErr_Occurred())
        {
            Py_DECREF(res);
            return -1;
        }
        if (timeout < 0)
        {
            timeout = -1.0;
        }
    }

    Py_DECREF(res);

    res = PyObject_CallMethod(_sock, "settimeout", "d", (double)0.0);
    Py_XDECREF(res);
    if (res == NULL)
    {
        return -1;
    }

    Py_INCREF(_sock);
    Py_XSETREF(self->_sock, _sock);
    self->_sock_fd = fileno;
    self->timeout = timeout;

    return 0;
}

#if _FIL_PYTHON3
static inline int _sock_init_from_fileno(PyFilSocket *self, SOCKET_T fileno)
{
    PyObject *_sock;

    _sock = _new_internal_socket(self->family, self->type, self->proto, fileno);
    if (_sock == NULL)
    {
        return -1;
    }

    return _sock_init_from_sock_and_fileno(self, _sock, fileno);
}

static inline PyObject *_create_new_socket_from_fileno(int family, int type, int proto, SOCKET_T fileno)
{
    PyFilSocket *sock;

    sock = _sock_new(_PYFIL_SOCK_TYPE, NULL, NULL);
    if (sock == NULL)
    {
        return NULL;
    }

    if (_sock_init_from_fileno(sock, fileno) < 0)
    {
        Py_DECREF(sock);
        return NULL;
    }

    sock->family = family;
    sock->type = type;
    sock->proto = proto;

    return (PyObject *)sock;
}

static inline PyObject *_create_new_socket_from_fileno_obj(int family, int type, int proto, PyObject *fileno_obj)
{
    SOCKET_T fileno;

    if (_fileno_from_obj(fileno_obj, &fileno) < 0) {
        return NULL;
    }

    return _create_new_socket_from_fileno(family, type, proto, fileno);
}
#endif

static inline PyObject *_create_new_socket_from_sock(int family, int type, int proto, PyObject *_sock)
{
    PyFilSocket *sock;
    SOCKET_T fileno = -1;

    sock = _sock_new(_PYFIL_SOCK_TYPE, NULL, NULL);
    if (sock == NULL)
    {
        return NULL;
    }

    if (_fileno_from_internal_socket(_sock, &fileno) < 0)
    {
        Py_DECREF(sock);
        return NULL;
    }

    if (_sock_init_from_sock_and_fileno(sock, _sock, fileno) < 0)
    {
        Py_DECREF(sock);
        return NULL;
    }

    sock->family = family;
    sock->type = type;
    sock->proto = proto;

    return (PyObject *)sock;
}

static int _sock_init(PyFilSocket *self, PyObject *args, PyObject *kwargs)
{
    int family = AF_INET, type = SOCK_STREAM, proto = 0;
    static char *keywords[] = {
        "family",
        "type",
        "proto",
#ifdef _FIL_PYTHON3
        "fileno",
#else
        "_sock",
#endif
        NULL,
    };
    PyObject *fileno_or_sock = NULL;
    PyObject *_sock = NULL;
    SOCKET_T fileno = -1;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs,
        "|iiiO:socket",
        keywords,
        &family,
        &type,
        &proto,
        &fileno_or_sock))
    {
        return -1;
    }

    if (fileno_or_sock != NULL && fileno_or_sock != Py_None)
    {
#if _FIL_PYTHON3
        if (_fileno_from_obj(fileno_or_sock, &fileno) < 0)
        {
            return -1;
        }
        _sock = _new_internal_socket(family, type, proto, fileno);
#else
        _sock = fileno_or_sock;
        Py_INCREF(_sock);
#endif
    } else {
        _sock = _new_internal_socket(family, type, proto, fileno);
    }

    if (fileno < 0)
    {
        if (_fileno_from_internal_socket(_sock, &fileno) < 0)
        {
            Py_DECREF(_sock);
            return -1;
        }
    }

    if (_sock_init_from_sock_and_fileno(self, _sock, fileno) < 0)
    {
        Py_DECREF(_sock);
        return -1;
    }

    Py_DECREF(_sock);

    self->family = family;
    self->type = type;
    self->proto = proto;
    _sock_clear_methods(self);
    return 0;
}

static void _sock_dealloc(PyFilSocket *self)
{
    _sock_clear_methods(self);
    Py_CLEAR(self->_sock);

    PyObject_Del(self);
}

PyDoc_STRVAR(_sock_accept_doc,
"accept() -> (socket object, address info)\n\
\n\
Wait for an incoming connection.  Return a new socket representing the\n\
connection, and the address of the client.  For IP sockets, the address\n\
info is a pair (hostaddr, port).");

#if _FIL_PYTHON3
FIL_CPROXY_POLL(_accept, (PyFilSocket *self), (attr, _EMPTY_TUPLE, NULL), read)
#else
FIL_CPROXY_POLL(accept, (PyFilSocket *self), (attr, _EMPTY_TUPLE, NULL), read)
#endif

static PyObject *_sock_accept_real(PyFilSocket *self)
{
    PyObject *sock;
#if _FIL_PYTHON3
    PyObject *res = _sock__accept(self);
#else
    PyObject *res = _sock_accept(self);
#endif

    if (res == NULL || !PyTuple_Check(res) || PyTuple_GET_SIZE(res) < 1)
    {
        return res;
    }

#if _FIL_PYTHON3
    sock = _create_new_socket_from_fileno_obj(self->family, self->type, self->proto, PyTuple_GET_ITEM(res, 0));
#else
    sock = _create_new_socket_from_sock(self->family, self->type, self->proto, PyTuple_GET_ITEM(res, 0));
#endif
    if (sock == NULL)
    {
        Py_DECREF(res);
        return NULL;
    }

    /* steals reference, even on failure */
    if (PyTuple_SetItem(res, 0, sock) < 0)
    {
        Py_DECREF(res);
        return NULL;
    }

    return res;
}

PyDoc_STRVAR(_sock_bind_doc,
"bind(address)\n\
\n\
Bind the socket to a local address.  For IP sockets, the address is a\n\
pair (host, port); the host must refer to the local host. For raw packet\n\
sockets the address is a tuple (ifname, proto [,pkttype [,hatype]])");
FIL_PROXY_ARG(bind)

PyDoc_STRVAR(_sock_close_doc,
"close()\n\
\n\
Close the socket.  It cannot be used after this call.");
static PyObject *_sock_close(PyFilSocket *self)
{
    PyObject *close_meth, *res;

    if (self->_sock_fd == INVALID_SOCKET)
    {
        Py_RETURN_NONE;
    }

    if ((close_meth = PyObject_GetAttrString(self->_sock, "close")) == NULL)
    {
        return NULL;
    }

    res = PyObject_Call(close_meth, _EMPTY_TUPLE, NULL);
    Py_DECREF(close_meth);

    if (res != NULL)
    {
        self->_sock_fd = INVALID_SOCKET;
    }

    return res;
}

#if _FIL_PYTHON3
PyDoc_STRVAR(_sock_detach_doc,
"detach()\n\
\n\
Close the socket object without closing the underlying file descriptor.\n\
The object cannot be used after this call, but the file descriptor\n\
can be reused for other purposes.  The file descriptor is returned.");

static PyObject *_sock_detach(PyFilSocket *self)
{
    PyObject *detach_meth, *res;

    if (self->_sock_fd == INVALID_SOCKET)
    {
        Py_RETURN_NONE;
    }

    if ((detach_meth = PyObject_GetAttrString(self->_sock, "detach")) == NULL)
    {
        return NULL;
    }

    res = PyObject_Call(detach_meth, _EMPTY_TUPLE, NULL);
    Py_DECREF(detach_meth);

    if (res != NULL)
    {
        self->_sock_fd = INVALID_SOCKET;
    }

    return res;
}
#endif

PyDoc_STRVAR(_sock_connect_doc,
"connect(address)\n\
\n\
Connect the socket to a remote address.  For IP sockets, the address\n\
is a pair (host, port).");
static PyObject *_sock_connect(PyFilSocket *self, PyObject *args)
{
    PyObject *res;
    PyObject *connect_meth;
    PyFilIOThread *iothr;
    struct timespec ts_buf, *ts;
    int err;
    socklen_t err_sz;

    connect_meth = PyObject_GetAttrString(self->_sock, "connect");
    if (connect_meth == NULL)
    {
        return NULL;
    }

    res = PyObject_Call(connect_meth, args, NULL);
    Py_DECREF(connect_meth);

    if ((res != NULL) || (self->timeout == 0.0))
    {
        return res;
    }

    if (!_exc_is_inprogress(1))
    {
        return NULL;
    }

    if (fil_timespec_from_double_interval(self->timeout, &ts_buf, &ts))
    {
        return NULL;
    }

    iothr = fil_iothread_get();
    if (iothr == NULL)
    {
        return NULL;
    }

    if ((err = fil_iothread_write_ready(iothr, self->_sock_fd, ts, _SOCK_TIMEOUT)))
    {
        Py_DECREF(iothr);
        return NULL;
    }

    Py_DECREF(iothr);

    err_sz = sizeof(err);
    err = 0;
    (void)getsockopt(self->_sock_fd, SOL_SOCKET, SO_ERROR, &err, &err_sz);
    if ((err == 0) || (err == EISCONN))
    {
        Py_RETURN_NONE;
    }

    errno = err;
    return PyErr_SetFromErrno(_SOCK_ERROR);
}

PyDoc_STRVAR(_sock_connect_ex_doc,
"connect_ex(address) -> errno\n\
\n\
This is like connect(address), but returns an error code (the errno value)\n\
instead of raising an exception when an error occurs.");
static PyObject *_sock_connect_ex(PyFilSocket *self, PyObject *args)
{
    PyObject *res;
    PyObject *connect_meth;
    PyFilIOThread *iothr;
    struct timespec ts_buf, *ts;
    int err;
    socklen_t err_sz;

    connect_meth = PyObject_GetAttrString(self->_sock, "connect_ex");
    if (connect_meth == NULL)
    {
        return NULL;
    }

    res = PyObject_Call(connect_meth, args, NULL);
    Py_DECREF(connect_meth);

    if ((res == NULL) || (self->timeout == 0.0))
    {
        return res;
    }

    err = PyInt_AsLong(res);
    if (err == -1 && PyErr_Occurred())
    {
        Py_DECREF(res);
        PyErr_SetString(PyExc_ValueError, "Received unexpected type from connect_ex() call");
        return NULL;
    }

    if (err != EINPROGRESS)
    {
        return res;
    }

    Py_DECREF(res);

    if (fil_timespec_from_double_interval(self->timeout, &ts_buf, &ts))
    {
        return NULL;
    }

    iothr = fil_iothread_get();
    if (iothr == NULL)
    {
        return NULL;
    }

    if ((err = fil_iothread_write_ready(iothr, self->_sock_fd, ts, _SOCK_TIMEOUT)))
    {
        Py_DECREF(iothr);
        if (PyErr_ExceptionMatches(_SOCK_TIMEOUT))
        {
            PyErr_Clear();
#ifdef EWOULDBLOCK
            return PyInt_FromLong((long)EWOULDBLOCK);
#else
            return PyInt_FromLong((long)EAGAIN);
#endif
        }
        return NULL;
    }

    Py_DECREF(iothr);

    err_sz = sizeof(err);
    err = 0;
    (void)getsockopt(self->_sock_fd, SOL_SOCKET, SO_ERROR, &err, &err_sz);
    if (err == EISCONN)
    {
        err = 0;
    }
    return PyInt_FromLong((long)err);
}

PyDoc_STRVAR(_sock_dup_doc,
"dup() -> socket object\n\
\n\
Return a new socket object connected to the same system resource.");
#if _FIL_PYTHON3
static PyObject *_sock_dup_real(PyFilSocket *self)
{
    PyObject *res;
    SOCKET_T new_fd;

    new_fd = _Py_dup(self->_sock_fd);
    if (new_fd == INVALID_SOCKET)
    {
        return NULL;
    }

    res = _create_new_socket_from_fileno(self->family, self->type, self->proto, new_fd);
    if (res == NULL)
    {
        close(new_fd);
    }
    return res;
}
#else
FIL_PROXY_NOARG(dup)

static PyObject *_sock_dup_real(PyFilSocket *self)
{
    PyObject *sock;
    PyObject *_sock = _sock_dup(self);

    if (_sock == NULL)
    {
        return NULL;
    }

    sock = _create_new_socket_from_sock(self->family, self->type, self->proto, _sock);

    Py_DECREF(_sock);

    if (sock == NULL)
    {
        return NULL;
    }

    return sock;
}
#endif

PyDoc_STRVAR(_sock_listen_doc,
"listen(backlog)\n\
\n\
Enable a server to accept connections.  The backlog argument must be at\n\
least 0 (if it is lower, it is set to 0); it specifies the number of\n\
unaccepted connections that the system will allow before refusing new\n\
connections.");
FIL_PROXY_ARG(listen)

PyDoc_STRVAR(_sock_fileno_doc,
"fileno() -> integer\n\
\n\
Return the integer file descriptor of the socket.");
static PyObject *_sock_fileno(PyFilSocket *self)
{
    #if SIZEOF_SOCKET_T <= SIZEOF_LONG
        return PyInt_FromLong((long) self->_sock_fd);
    #else
        return PyLong_FromLongLong((PY_LONG_LONG)self->_sock_fd);
    #endif
}

PyDoc_STRVAR(_sock_getpeername_doc,
"getpeername() -> address info\n\
\n\
Return the address of the remote endpoint.  For IP sockets, the address\n\
info is a pair (hostaddr, port).");
FIL_CPROXY_NOARG(getpeername)

PyDoc_STRVAR(_sock_getsockname_doc,
"getsockname() -> address info\n\
\n\
Return the address of the local endpoint.  For IP sockets, the address\n\
info is a pair (hostaddr, port).");
FIL_CPROXY_NOARG(getsockname)

PyDoc_STRVAR(_sock_getsockopt_doc,
"getsockopt(level, option[, buffersize]) -> value\n\
\n\
Get a socket option.  See the Unix manual for level and option.\n\
If a nonzero buffersize argument is given, the return value is a\n\
string of that length; otherwise it is an integer.");
FIL_CPROXY_VARG(getsockopt)

PyDoc_STRVAR(_sock_gettimeout_doc,
"gettimeout() -> timeout\n\
\n\
Returns the timeout in seconds (float) associated with socket \n\
operations. A timeout of None indicates that timeouts on socket \n\
operations are disabled.");
/* s.gettimeout() method.
   Returns the timeout associated with a socket. */
static PyObject *_sock_gettimeout(PyFilSocket *self)
{
    if (self->timeout < 0.0) {
        Py_RETURN_NONE;
    }
    return PyFloat_FromDouble(self->timeout);
}

#if defined(MS_WINDOWS) && defined(SIO_RCVALL)

PyDoc_STRVAR(_sock_ioctl_doc,
"ioctl(cmd, option) -> long\n\
\n\
Control the socket with WSAIoctl syscall. Currently supported 'cmd' values are\n\
SIO_RCVALL:  'option' must be one of the socket.RCVALL_* constants.\n\
SIO_KEEPALIVE_VALS:  'option' is a tuple of (onoff, timeout, interval).");
FIL_CPROXY_VARG(ioctl)

#endif

static inline ssize_t _sock_recv_common(PyFilSocket *self, char *buf, int len, int flags)
{
    ssize_t outlen;
    PyFilIOThread *iothr;
    struct timespec ts_buf, *ts;

    if (self->timeout == 0.0 || self->flags & PYFIL_SOCKET_FLAGS_TRY_WITHOUT_POLL)
    {
        Py_BEGIN_ALLOW_THREADS

        outlen = recv(self->_sock_fd, buf, len, flags);

        Py_END_ALLOW_THREADS

        if ((outlen >= 0) || self->timeout == 0.0 || !FIL_IS_EAGAIN(errno))
        {
            if (outlen < 0)
            {
                PyErr_SetFromErrno(_SOCK_ERROR);
            }
            return outlen;
        }

        self->first_misses++;
    }

    if (fil_timespec_from_double_interval(self->timeout, &ts_buf, &ts))
    {
        return -1;
    }

    iothr = fil_iothread_get();
    if (iothr == NULL)
    {
        return -1;
    }

    if (self->flags & PYFIL_SOCKET_FLAGS_DO_IN_BACKGROUND)
    {
        while (1)
        {
            outlen = fil_iothread_recv(iothr, self->_sock_fd, buf, len, flags, ts, _SOCK_TIMEOUT);
            if (outlen >= 0 || PyErr_Occurred() || !FIL_IS_EAGAIN(errno))
            {
                break;
            }
        }
    }
    else
    {
        while(1)
        {
            if (fil_iothread_read_ready(iothr, self->_sock_fd, ts, _SOCK_TIMEOUT))
            {
                outlen = -1;
                break;
            }

            Py_BEGIN_ALLOW_THREADS

            outlen = recv(self->_sock_fd, buf, len, flags);

            Py_END_ALLOW_THREADS

            if ((outlen >= 0) || !FIL_IS_EAGAIN(errno))
            {
                break;
            }
        }
    }

    Py_DECREF(iothr);

    if (outlen < 0 && !PyErr_Occurred())
    {
        PyErr_SetFromErrno(_SOCK_ERROR);
    }
    return outlen;
}

PyDoc_STRVAR(_sock_recv_doc,
"recv(buffersize[, flags]) -> data\n\
\n\
Receive up to buffersize bytes from the socket.  For the optional flags\n\
argument, see the Unix manual.  When no data is available, block until\n\
at least one byte is available or until the remote end is closed.  When\n\
the remote end is closed and all data is read, return the empty string.");
/* s.recv(nbytes [,flags]) method */
static PyObject *_sock_recv(PyFilSocket *self, PyObject *args)
{
    int recvlen, flags = 0;
    ssize_t outlen;
    PyObject *buf;

    if (!PyArg_ParseTuple(args, "i|i:recv", &recvlen, &flags))
    {
        return NULL;
    }

    if (recvlen < 0)
    {
        PyErr_SetString(PyExc_ValueError,
                        "negative buffersize in recv");
        return NULL;
    }

    /* Allocate a new string. */
    buf = PyString_FromStringAndSize((char *) 0, recvlen);
    if (buf == NULL)
    {
        return NULL;
    }

    outlen = _sock_recv_common(self, PyString_AS_STRING(buf), recvlen, flags);
    if (outlen < 0)
    {
        Py_DECREF(buf);
        return NULL;
    }

    if (outlen != recvlen)
    {
        if (_PyString_Resize(&buf, outlen) < 0)
        {
            /* a failure nukes the original */
            return NULL;
        }
    }

    return buf;
}


PyDoc_STRVAR(_sock_recv_into_doc,
"recv_into(buffer, [nbytes[, flags]]) -> nbytes_read\n\
\n\
A version of recv() that stores its data into a buffer rather than creating \n\
a new string.  Receive up to buffersize bytes from the socket.  If buffersize \n\
is not specified (or 0), receive up to the size available in the given buffer.\n\
\n\
See recv() for documentation about the flags.");
/* s.recv_into(buffer, [nbytes [,flags]]) method */
static PyObject *_sock_recv_into(PyFilSocket *self, PyObject *args, PyObject *kwargs)
{
    static char *kwlist[] = {"buffer", "nbytes", "flags", 0};

    int recvlen = 0, flags = 0;
    ssize_t readlen;
    Py_buffer buf;
    Py_ssize_t buflen;

    /* Get the buffer's memory */
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "w*|ii:recv_into", kwlist,
                                     &buf, &recvlen, &flags))
    {
        return NULL;
    }

    buflen = buf.len;
    assert(buf.buf != 0 && buflen > 0);

    if (recvlen < 0)
    {
        PyErr_SetString(PyExc_ValueError,
                        "negative buffersize in recv_into");
        goto error;
    }
    if (recvlen == 0)
    {
        /* If nbytes was not specified, use the buffer's length */
        recvlen = buflen;
    }

    /* Check if the buffer is large enough */
    if (buflen < recvlen)
    {
        PyErr_SetString(PyExc_ValueError,
                        "buffer too small for requested bytes");
        goto error;
    }

    /* Call the guts */
    readlen = _sock_recv_common(self, buf.buf, recvlen, flags);
    if (readlen < 0) {
        /* Return an error. */
        goto error;
    }

    PyBuffer_Release(&buf);
    /* Return the number of bytes read.  Note that we do not do anything
       special here in the case that readlen < recvlen. */
    return PyInt_FromSsize_t(readlen);

error:
    PyBuffer_Release(&buf);
    return NULL;
}

PyDoc_STRVAR(_sock_recvfrom_doc,
"recvfrom(buffersize[, flags]) -> (data, address info)\n\
\n\
Like recv(buffersize, flags) but also return the sender's address info.");
/* s.recvfrom(nbytes [,flags]) method */
FIL_CPROXY_POLL(
        recvfrom,
        (PyFilSocket *self, PyObject *args),
        (attr, args, NULL),
        read
)

PyDoc_STRVAR(_sock_recvfrom_into_doc,
"recvfrom_into(buffer[, nbytes[, flags]]) -> (nbytes, address info)\n\
\n\
Like recv_into(buffer[, nbytes[, flags]]) but also return the sender's address info.");
/* s.recvfrom_into(buffer[, nbytes [,flags]]) method */
FIL_CPROXY_POLL(
        recvfrom_into,
        (PyFilSocket *self, PyObject *args, PyObject *kwargs),
        (attr, args, kwargs),
        read
)

static inline ssize_t _sock_send_common(PyFilSocket *self, char *buf, int len, int flags, struct timespec *ts_buf, struct timespec **tsptr)
{
    ssize_t outlen;
    PyFilIOThread *iothr;

    if (ts_buf != NULL)
    {
        if (self->timeout == 0.0 || self->flags & PYFIL_SOCKET_FLAGS_TRY_WITHOUT_POLL)
        {
            Py_BEGIN_ALLOW_THREADS

            outlen = send(self->_sock_fd, buf, len, flags);

            Py_END_ALLOW_THREADS

            if ((outlen >= 0) || self->timeout == 0.0 || !FIL_IS_EAGAIN(errno))
            {
                if (outlen < 0)
                {
                    PyErr_SetFromErrno(_SOCK_ERROR);
                }
                return outlen;
            }

            self->first_misses++;
        }

        if (fil_timespec_from_double_interval(self->timeout, ts_buf, tsptr))
        {
            return -1;
        }
    }

    iothr = fil_iothread_get();
    if (iothr == NULL)
    {
        return -1;
    }

    if (self->flags & PYFIL_SOCKET_FLAGS_DO_IN_BACKGROUND)
    {
        while (1)
        {
            outlen = fil_iothread_send(iothr, self->_sock_fd, buf, len, flags, *tsptr, _SOCK_TIMEOUT);
            if (outlen >= 0 || PyErr_Occurred() || !FIL_IS_EAGAIN(errno))
            {
                break;
            }
        }
    }
    else
    {
        while(1)
        {
            if (fil_iothread_write_ready(iothr, self->_sock_fd, *tsptr, _SOCK_TIMEOUT))
            {
                outlen = -1;
                break;
            }

            Py_BEGIN_ALLOW_THREADS

            outlen = send(self->_sock_fd, buf, len, flags);

            Py_END_ALLOW_THREADS

            if ((outlen >= 0) || !FIL_IS_EAGAIN(errno))
            {
                break;
            }
        }
    }

    Py_DECREF(iothr);

    if (outlen < 0 && !PyErr_Occurred())
    {
        PyErr_SetFromErrno(_SOCK_ERROR);
    }
    return outlen;
}

PyDoc_STRVAR(_sock_send_doc,
"send(data[, flags]) -> count\n\
\n\
Send a data string to the socket.  For the optional flags\n\
argument, see the Unix manual.  Return the number of bytes\n\
sent; this may be less than len(data) if the network is busy.");
/* s.send(data [,flags]) method */
static PyObject *_sock_send(PyFilSocket *self, PyObject *args)
{
    int flags = 0;
    Py_buffer pbuf;
    ssize_t outlen;
    struct timespec ts_buf, *ts = NULL;

    if (!PyArg_ParseTuple(args, "s*|i:send", &pbuf, &flags))
    {
        return NULL;
    }

    outlen = _sock_send_common(self, pbuf.buf, pbuf.len, flags, &ts_buf, &ts);

    PyBuffer_Release(&pbuf);

    if (outlen < 0)
    {
        return NULL;
    }

    return PyInt_FromSsize_t(outlen);
}

PyDoc_STRVAR(_sock_sendall_doc,
"sendall(data[, flags])\n\
\n\
Send a data string to the socket.  For the optional flags\n\
argument, see the Unix manual.  This calls send() repeatedly\n\
until all data is sent.  If an error occurs, it's impossible\n\
to tell how much data has been sent.");
/* s.sendall(data [,flags]) method */
static PyObject *_sock_sendall(PyFilSocket *self, PyObject *args)
{
    Py_buffer pbuf;
    int flags = 0;
    int len;
    char *buf;
    struct timespec ts_buf, *ts = NULL;
    ssize_t outlen;

    if (!PyArg_ParseTuple(args, "s*|i:sendall", &pbuf, &flags))
    {
        return NULL;
    }

    buf = pbuf.buf;
    len = pbuf.len;

    outlen = _sock_send_common(self, buf, len, flags, &ts_buf, &ts);
    if (outlen >= 0 && outlen < len)
    {
        buf += outlen;
        len -= outlen;
        do {
            outlen = _sock_send_common(self, buf, len, flags, NULL, &ts);
            if (outlen < 0)
            {
                break;
            }

            buf += outlen;
            len -= outlen;
        } while(len > 0);
    }

    PyBuffer_Release(&pbuf);

    if (outlen < 0)
    {
        return NULL;
    }

    Py_RETURN_NONE;
}

PyDoc_STRVAR(_sock_sendto_doc,
"sendto(data[, flags], address) -> count\n\
\n\
Like send(data, flags) but allows specifying the destination address.\n\
For IP sockets, the address is a pair (hostaddr, port).");
/* s.sendto(data, [flags,] sockaddr) method */
FIL_CPROXY_POLL(
        sendto,
        (PyFilSocket *self, PyObject *args),
        (attr, args, NULL),
        write
)

PyDoc_STRVAR(_sock_setblocking_doc,
"setblocking(flag)\n\
\n\
Set the socket to blocking (flag is true) or non-blocking (false).\n\
setblocking(True) is equivalent to settimeout(None);\n\
setblocking(False) is equivalent to settimeout(0.0).");
static PyObject *_sock_setblocking(PyFilSocket *self, PyObject *arg)
{
    long block;

    block = PyInt_AsLong(arg);
    if (block == -1 && PyErr_Occurred())
    {
        return NULL;
    }

    self->timeout = block ? -1.0 : 0.0;

    Py_RETURN_NONE;
}

PyDoc_STRVAR(_sock_setsockopt_doc,
"setsockopt(level, option, value)\n\
\n\
Set a socket option.  See the Unix manual for level and option.\n\
The value argument can either be an integer or a string.");
FIL_PROXY_VARG(setsockopt)

PyDoc_STRVAR(_sock_settimeout_doc,
"settimeout(timeout)\n\
\n\
Set a timeout on socket operations.  'timeout' can be a float,\n\
giving in seconds, or None.  Setting a timeout of None disables\n\
the timeout feature and is equivalent to setblocking(1).\n\
Setting a timeout of zero is the same as setblocking(0).");
/* s.settimeout(timeout) method.  Argument:
   None -- no timeout, blocking mode; same as setblocking(True)
   0.0  -- non-blocking mode; same as setblocking(False)
   > 0  -- timeout mode; operations time out after timeout seconds
   < 0  -- illegal; raises an exception
*/
static PyObject *_sock_settimeout(PyFilSocket *self, PyObject *arg)
{
    double timeout;

    if (arg == Py_None)
        timeout = -1.0;
    else {
        timeout = PyFloat_AsDouble(arg);
        if (timeout < 0.0) {
            if (!PyErr_Occurred())
                PyErr_SetString(PyExc_ValueError,
                                "Timeout value out of range");
            return NULL;
        }
    }

    self->timeout = timeout;

    Py_RETURN_NONE;
}

PyDoc_STRVAR(_sock_shutdown_doc,
"shutdown(flag)\n\
\n\
Shut down the reading side of the socket (flag == SHUT_RD), the writing side\n\
of the socket (flag == SHUT_WR), or both ends (flag == SHUT_RDWR).");
FIL_PROXY_ARG(shutdown)

static PyMethodDef _sock_methods[] = {
    { "accept", (PyCFunction)_sock_accept_real, METH_NOARGS, _sock_accept_doc },
    { "bind", (PyCFunction)_sock_bind, METH_O, _sock_bind_doc },
    { "close", (PyCFunction)_sock_close, METH_NOARGS, _sock_close_doc },
    { "connect", (PyCFunction)_sock_connect, METH_VARARGS, _sock_connect_doc },
    { "connect_ex", (PyCFunction)_sock_connect_ex, METH_VARARGS, _sock_connect_ex_doc },
    { "detach", (PyCFunction)_sock_detach, METH_NOARGS, _sock_detach_doc },
    { "dup", (PyCFunction)_sock_dup_real, METH_NOARGS, _sock_dup_doc },
    { "fileno", (PyCFunction)_sock_fileno, METH_NOARGS, _sock_fileno_doc },
    { "getpeername", (PyCFunction)_sock_getpeername, METH_NOARGS, _sock_getpeername_doc },
    { "getsockname", (PyCFunction)_sock_getsockname, METH_NOARGS, _sock_getsockname_doc },
    { "getsockopt", (PyCFunction)_sock_getsockopt, METH_VARARGS, _sock_getsockopt_doc },
    { "gettimeout", (PyCFunction)_sock_gettimeout, METH_NOARGS, _sock_gettimeout_doc },
#if defined(MS_WINDOWS) && defined(SIO_RCVALL)
    { "ioctl", (PyCFunction)_sock_ioctl, METH_VARARGS, _sock_ioctl_doc },
#endif
    { "listen", (PyCFunction)_sock_listen, METH_O, _sock_listen_doc },
    /* No 'makefile'. Doesn't exist in py3 here, and py2 ignores it in favor
     * of the one in socket.py
     */
    { "recv", (PyCFunction)_sock_recv, METH_VARARGS, _sock_recv_doc },
    { "recv_into", (PyCFunction)_sock_recv_into, METH_VARARGS|METH_KEYWORDS, _sock_recv_into_doc },
    { "recvfrom", (PyCFunction)_sock_recvfrom, METH_VARARGS, _sock_recvfrom_doc },
    { "recvfrom_into", (PyCFunction)_sock_recvfrom_into, METH_VARARGS|METH_KEYWORDS, _sock_recvfrom_into_doc},
    { "send", (PyCFunction)_sock_send, METH_VARARGS, _sock_send_doc },
    { "sendall", (PyCFunction)_sock_sendall, METH_VARARGS, _sock_sendall_doc },
    { "sendto", (PyCFunction)_sock_sendto, METH_VARARGS, _sock_sendto_doc },
    { "setblocking", (PyCFunction)_sock_setblocking, METH_O, _sock_setblocking_doc },
    { "setsockopt", (PyCFunction)_sock_setsockopt, METH_VARARGS, _sock_setsockopt_doc },
    { "settimeout", (PyCFunction)_sock_settimeout, METH_O, _sock_settimeout_doc },
    { "shutdown", (PyCFunction)_sock_shutdown, METH_O, _sock_shutdown_doc },
    { NULL, },
};

static PyMemberDef _sock_memberlist[] = {
    { "family", T_INT, offsetof(PyFilSocket, family), READONLY, "the socket family" },
    { "type", T_INT, offsetof(PyFilSocket, type), READONLY, "the socket type" },
    { "proto", T_INT, offsetof(PyFilSocket, proto), READONLY, "the socket protocol" },
    { "_sock", T_OBJECT, offsetof(PyFilSocket, _sock), READONLY, "the real _socket.socket that filament has wrapped" },
    { "fil_first_misses", T_INT, offsetof(PyFilSocket, first_misses), READONLY, "how many misses on try before poll" },
    { NULL, },
};

static PyTypeObject _sock_type = {
    PyVarObject_HEAD_INIT(0, 0)
    "_filament.socket.Socket",                  /* tp_name */
    sizeof(PyFilSocket),                        /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_sock_dealloc,                  /* tp_dealloc */
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
    _sock_methods,                              /* tp_methods */
    _sock_memberlist,                           /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_sock_init,                       /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_sock_new,                         /* tp_new */
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

PyDoc_STRVAR(_socket_fil_set_resolver_doc,
"fil_set_resolver(resolver) -> None\n\
\n\
Set the async resolver class to use.");
static PyObject *_socket_fil_set_resolver(PyObject *self, PyObject *arg)
{
    if (_check_resolver_methods(arg))
    {
        return NULL;
    }
    if (_copy_resolver_methods(arg))
    {
        return NULL;
    }
    Py_INCREF(arg);
    Py_XSETREF(_RESOLVER, arg);
    Py_RETURN_NONE;
}

PyDoc_STRVAR(_socket_fil_resolver_method_list_doc,
"fil_resolver_method_list() -> list\n\
\n\
Returns the list of methods proxied to the resolver class.");
static PyObject *_socket_fil_resolver_method_list(PyObject *self)
{
    return Py_INCREF(_RESOLVER_METHOD_LIST), _RESOLVER_METHOD_LIST;
}

PyDoc_STRVAR(_socket_socketpair_doc,
"socketpair([family[, type[, proto]]]) -> (socket object, socket object)\n\
\n\
Create a pair of socket objects from the sockets returned by the platform\n\
socketpair() function.\n\
The arguments are the same as for socket() except the default family is\n\
AF_UNIX if defined on the platform; otherwise, the default is AF_INET.");
static PyObject *_socket_socketpair(PyObject *self, PyObject *args)
{
    PyObject *sock1, *sock2;
    PyObject *res;
    int family, type = SOCK_STREAM, proto = 0;

#ifdef AF_UNIX
    family = AF_UNIX;
#else
    family = AF_INET;
#endif

    if (!PyArg_ParseTuple(args, "|iii:socketpair", &family, &type, &proto))
    {
        return NULL;
    }

    res = PyObject_Call(_SOCK_SOCKETPAIR, args, NULL);
    if (res == NULL || !PyTuple_Check(res) || PyTuple_GET_SIZE(res) != 2)
    {
        return res;
    }

    sock1 = _create_new_socket_from_sock(family, type, proto, PyTuple_GET_ITEM(res, 0));
    if (sock1 == NULL)
    {
        Py_DECREF(res);
        return NULL;
    }

    sock2 = _create_new_socket_from_sock(family, type, proto, PyTuple_GET_ITEM(res, 1));
    if (sock2 == NULL)
    {
        Py_DECREF(sock1);
        Py_DECREF(res);
        return NULL;
    }

    /* steals sock1 reference even on failure */
    if (PyTuple_SET_ITEM(res, 0, sock1) < 0)
    {
        Py_DECREF(sock2);
        Py_DECREF(res);
        return NULL;
    }

    /* steals sock2 reference even on failure */
    if (PyTuple_SET_ITEM(res, 1, sock2) < 0)
    {
        Py_DECREF(res);
        return NULL;
    }

    return res;
}

/* doc strings set dynamically */
static PyObject *_socket_resolver_proxy_fn(PyObject *self, PyObject *args, PyObject *kwargs)
{
    /* we init the proxy methods with 'self' being the idx encoded in as a PyLong */
    long idx = (long)PyLong_AsLong(self);
    PyObject *fn, *res;

    if (idx < 0 || idx >= _NUM_RESOLVER_METHODS)
    {
        PyErr_SetString(PyExc_RuntimeError, "internal error: fil resolver proxy idx out of range");
        return NULL;
    }
    /* It's possible for a greenlet switch or the GIL to be released..
     * and one could change the resolver, so we'd better grab a ref.
     */
    fn = _RESOLVER_METHODS[idx];
    Py_INCREF(fn);
    res = PyObject_Call(fn, args, kwargs);
    Py_DECREF(fn);
    return res;
}

static PyMethodDef _fil_resolver_proxy_methods[_NUM_RESOLVER_METHODS];

PyDoc_STRVAR(_fil_socket_module_doc, "Filament _filament.socket module.");
static PyMethodDef _fil_socket_module_methods[] = {
    /* The above _fil_resolver_proxy_methods also get set on import */
    { "fil_set_resolver", (PyCFunction)_socket_fil_set_resolver, METH_O, _socket_fil_set_resolver_doc },
    { "fil_resolver_method_list", (PyCFunction)_socket_fil_resolver_method_list, METH_NOARGS, _socket_fil_resolver_method_list_doc },
    { "socketpair", (PyCFunction)_socket_socketpair, METH_VARARGS, _socket_socketpair_doc },
    { NULL, },
};

static int _init_resolver_proxy_fns(PyObject *module, char *mod_name_str)
{
    char **meth_name_ptr = _RESOLVER_METHOD_NAMES;
    PyObject **meth_ptr = _RESOLVER_METHODS;
    PyMethodDef *mdef = _fil_resolver_proxy_methods;
    PyObject *mod_name;
    PyObject *fn;
    PyObject *doc;
    PyObject *res_idx;
    char *doc_str;
    long idx;
    int res = 0;

    mod_name = PyString_FromString(mod_name_str);
    if (mod_name == NULL)
    {
        return -1;
    }

    for(idx = 0; *meth_name_ptr; mdef++, meth_name_ptr++, meth_ptr++, idx++)
    {
        doc_str = NULL;
        doc = PyObject_GetAttrString(*meth_ptr, "__doc__");
        if (doc != NULL && PyString_Check(doc))
        {
            doc_str = strdup(PyString_AS_STRING(doc));
        }

        Py_XDECREF(doc);

        mdef->ml_name = *meth_name_ptr;
        mdef->ml_meth = (PyCFunction)_socket_resolver_proxy_fn;
        mdef->ml_flags = METH_VARARGS | METH_KEYWORDS;
        mdef->ml_doc = doc_str ? doc_str : "<doc unavailable>";

        res_idx = PyLong_FromLong(idx);
        if (res_idx == NULL)
        {
            res = -1;
            break;
        }

        fn = PyCFunction_NewEx(mdef, res_idx, mod_name);

        Py_DECREF(res_idx);

        if (fn == NULL)
        {
            res = -1;
            break;
        }

        res = PyModule_AddObject(module, *meth_name_ptr, fn);
        if (res)
        {
            Py_DECREF(fn);
            break;
        }
    }

    Py_DECREF(mod_name);

    return res;
}

_FIL_MODULE_INIT_FN_NAME(socket)
{
    PyObject *m;
    int i;

    PyFilCore_Import();
    PyFilIO_Import();

    _EMPTY_TUPLE = fil_empty_tuple();

    _RESOLVER_METHOD_LIST = PyList_New(_NUM_RESOLVER_METHODS);
    if (_RESOLVER_METHOD_LIST == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }
    for(i = 0;i < _NUM_RESOLVER_METHODS;i++)
    {
        PyObject *str = PyString_FromString(_RESOLVER_METHOD_NAMES[i]);
        if (str == NULL)
        {
            Py_CLEAR(_RESOLVER_METHOD_LIST);
            return _FIL_MODULE_INIT_ERROR;
        }
        PyList_SET_ITEM(_RESOLVER_METHOD_LIST, i, str);
    }

    /* We wrap the internal socket */
    if (_SOCK_MODULE == NULL &&
        (_SOCK_MODULE = PyImport_ImportModuleNoBlock("_socket")) == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    if (_SOCK_CLASS == NULL &&
        (_SOCK_CLASS = PyObject_GetAttrString(_SOCK_MODULE, "socket")) == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    if (_SOCK_ERROR == NULL &&
        (_SOCK_ERROR = PyObject_GetAttrString(_SOCK_MODULE, "error")) == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    if (_SOCK_HERROR == NULL &&
        (_SOCK_HERROR = PyObject_GetAttrString(_SOCK_MODULE, "herror")) == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    if (_SOCK_TIMEOUT == NULL &&
        (_SOCK_TIMEOUT = PyObject_GetAttrString(_SOCK_MODULE, "timeout")) == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    if (_SOCK_SOCKETPAIR == NULL &&
        (_SOCK_SOCKETPAIR = PyObject_GetAttrString(_SOCK_MODULE, "socketpair")) == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    if (PyType_Ready(&_sock_type) < 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    _PYFIL_SOCK_TYPE = &_sock_type;

    _FIL_MODULE_SET(m, FIL_SOCKET_MODULE_NAME, _fil_socket_module_methods, _fil_socket_module_doc);
    if (m == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    Py_INCREF((PyObject *)&_sock_type);
    if (PyModule_AddObject(m, "Socket", (PyObject *)&_sock_type) != 0)
    {
        Py_DECREF((PyObject *)&_sock_type);
        return _FIL_MODULE_INIT_ERROR;
    }

    Py_INCREF((PyObject *)&_sock_type);
    if (PyModule_AddObject(m, "socket", (PyObject *)&_sock_type) != 0)
    {
        Py_DECREF((PyObject *)&_sock_type);
        return _FIL_MODULE_INIT_ERROR;
    }

    Py_INCREF((PyObject *)&_sock_type);
    if (PyModule_AddObject(m, "SocketType", (PyObject *)&_sock_type) != 0)
    {
        Py_DECREF((PyObject *)&_sock_type);
        return _FIL_MODULE_INIT_ERROR;
    }

    if (_RESOLVER == NULL)
    {
        PyObject *rm;
        char *ptr;

        ptr = getenv("FILAMENT_RESOLVER_MODULE");
        if (ptr == NULL)
        {
            ptr = FIL_DEFAULT_RESOLVER_CLASS;
        }

        if ((rm = PyImport_ImportModuleNoBlock(ptr)) == NULL)
        {
            return _FIL_MODULE_INIT_ERROR;
        }

        _RESOLVER = PyObject_CallMethod(rm, "get_resolver", "");
        Py_DECREF(rm);

        if (_RESOLVER == NULL)
        {
            return _FIL_MODULE_INIT_ERROR;
        }

        if (_check_resolver_methods(_RESOLVER))
        {
            _clear_resolver_methods();
            Py_CLEAR(_RESOLVER);
            return _FIL_MODULE_INIT_ERROR;
        }

        if (_copy_resolver_methods(_RESOLVER))
        {
            _clear_resolver_methods();
            Py_CLEAR(_RESOLVER);
            return _FIL_MODULE_INIT_ERROR;
        }

        if (_init_resolver_proxy_fns(m, FIL_SOCKET_MODULE_NAME))
        {
            _clear_resolver_methods();
            Py_CLEAR(_RESOLVER);
            return _FIL_MODULE_INIT_ERROR;
        }
    }

    /* Now copy everything we don't have from original module */
    PyDict_Merge(PyModule_GetDict(m), PyModule_GetDict(_SOCK_MODULE), 0);

    return _FIL_MODULE_INIT_SUCCESS(m);
}
