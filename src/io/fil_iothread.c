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
#include <fcntl.h>
#include <event2/event.h>
#include <event2/util.h>
#include <event2/thread.h>

PyTypeObject *PyFilIOThread_Type = NULL;

typedef struct _pyfil_iothread
{
    PyObject_HEAD
    struct event_base *event_base;
    struct event *interrupt_event;
    PyThreadState *thread_state;
    pthread_t thr_id;
#define FIL_IOTHR_FLAGS_RUNNING  0x00000001
#define FIL_IOTHR_FLAGS_SHUTDOWN 0x00000002
    uint32_t flags;
} PyFilIOThread;

typedef int (*event_processor_t)(evutil_socket_t fd, void *processor_arg);

/*
 * Various structures to hold callback information. The corresponding
 * PyFilIOThread pointer needs to be the first thing in every structure.
 */
struct _read_info
{
    PyFilIOThread *iothr;
    ssize_t result;
    void *buffer;
    size_t buf_sz;
    int errn;
};

struct _write_info
{
    PyFilIOThread *iothr;
    ssize_t result;
    void *buffer;
    size_t buf_sz;
    int errn;
};

struct _accept_info
{
    PyFilIOThread *iothr;
    int result;
    struct sockaddr *address;
    socklen_t *address_len;
    int errn;
};

struct _connect_info
{
    PyFilIOThread *iothr;
    int result;
    int errn;
};

struct _recv_info
{
    PyFilIOThread *iothr;
    ssize_t result;
    void *buffer;
    size_t buf_sz;
    int flags;
    int errn;
};

struct _recvfrom_info
{
    PyFilIOThread *iothr;
    ssize_t result;
    void *buffer;
    size_t buf_sz;
    int flags;
    struct sockaddr *address;
    socklen_t *address_len;
    int errn;
};

struct _recvmsg_info
{
    PyFilIOThread *iothr;
    ssize_t result;
    struct msghdr *message;
    int flags;
    int errn;
};

struct _send_info
{
    PyFilIOThread *iothr;
    ssize_t result;
    void *buffer;
    size_t buf_sz;
    int flags;
    int errn;
};

struct _sendto_info
{
    PyFilIOThread *iothr;
    ssize_t result;
    void *buffer;
    size_t buf_sz;
    int flags;
    struct sockaddr *address;
    socklen_t address_len;
    int errn;
};

struct _sendmsg_info
{
    PyFilIOThread *iothr;
    ssize_t result;
    struct msghdr *message;
    int flags;
    int errn;
};

struct _event_cb_info
{
    pthread_mutex_t ecbi_lock;
    pthread_cond_t ecbi_cond;
    FilWaiter *waiter;
    struct event *event;
#define IOTHR_ECBI_FLAGS_WAITING  0x00000001
#define IOTHR_ECBI_FLAGS_TIMEOUT  0x00000002
#define IOTHR_ECBI_FLAGS_DONE     0x00000004
    uint32_t flags;
    event_processor_t processor;
    void *processor_arg;

    union
    {
        /* This pointer also needs to exist as the first
         * variable in every struct below. This allows us
         * to set 'iothr' here and be able to access it
         * properly in the corresponding struct.
         */
        PyFilIOThread *iothr;

        struct _read_info read_info;
        struct _write_info write_info;
        struct _accept_info accept_info;
        struct _connect_info connect_info;
        struct _recv_info recv_info;
        struct _recvfrom_info recvfrom_info;
        struct _recvmsg_info recvmsg_info;
        struct _send_info send_info;
        struct _sendto_info sendto_info;
        struct _sendmsg_info sendmsg_info;
    };
};


/*
 *
 *
 */

static PyFilIOThread *_IOThreadObj = NULL;

/*
 *
 *
 */

static int _read_processor(evutil_socket_t fd, struct _read_info *ri)
{
    ri->result = read(fd, ri->buffer, ri->buf_sz);
    if (ri->result == -1)
    {
        if (FIL_IS_EAGAIN(errno))
        {
            return -1;
        }

        ri->errn = errno;
    }

    return 0;
}

static int _write_processor(evutil_socket_t fd, struct _write_info *wi)
{
    wi->result = write(fd, wi->buffer, wi->buf_sz);
    if (wi->result == -1)
    {
        if (FIL_IS_EAGAIN(errno))
        {
            return -1;
        }

        wi->errn = errno;
    }

    return 0;
}

static int _accept_processor(evutil_socket_t fd, struct _accept_info *ai)
{
    ai->result = accept(fd, ai->address, ai->address_len);
    if (ai->result < 0)
    {
        if (FIL_IS_EAGAIN(errno))
        {
            return -1;
        }

        ai->errn = errno;
    }

    return 0;
}

static int _connect_processor(evutil_socket_t fd, struct _connect_info *ci)
{
    int res = 0;
    socklen_t res_size = sizeof(res);

    getsockopt(fd, SOL_SOCKET, SO_ERROR, &res, &res_size);
    if (res == 0 || res == EISCONN)
    {
        ci->result = 0;
    }
    else
    {
        ci->result = -1;
        ci->errn = res;
    }
    return 0;
}

static int _recv_processor(evutil_socket_t fd, struct _recv_info *ri)
{
    ri->result = recv(fd, ri->buffer, ri->buf_sz, ri->flags);
    if (ri->result == -1)
    {
        if (FIL_IS_EAGAIN(errno))
        {
            return -1;
        }

        ri->errn = errno;
    }

    return 0;
}

static int _recvfrom_processor(evutil_socket_t fd, struct _recvfrom_info *ri)
{
    ri->result = recvfrom(fd, ri->buffer, ri->buf_sz, ri->flags,
                          ri->address, ri->address_len);
    if (ri->result == -1)
    {
        if (FIL_IS_EAGAIN(errno))
        {
            return -1;
        }

        ri->errn = errno;
    }

    return 0;
}

static int _recvmsg_processor(evutil_socket_t fd, struct _recvmsg_info *ri)
{
    ri->result = recvmsg(fd, ri->message, ri->flags);
    if (ri->result == -1)
    {
        if (FIL_IS_EAGAIN(errno))
        {
            return -1;
        }

        ri->errn = errno;
    }

    return 0;
}

static int _send_processor(evutil_socket_t fd, struct _send_info *ri)
{
    ri->result = send(fd, ri->buffer, ri->buf_sz, ri->flags);
    if (ri->result == -1)
    {
        if (FIL_IS_EAGAIN(errno))
        {
            return -1;
        }

        ri->errn = errno;
    }

    return 0;
}

static int _sendto_processor(evutil_socket_t fd, struct _sendto_info *ri)
{
    ri->result = sendto(fd, ri->buffer, ri->buf_sz, ri->flags,
                        ri->address, ri->address_len);
    if (ri->result == -1)
    {
        if (FIL_IS_EAGAIN(errno))
        {
            return -1;
        }

        ri->errn = errno;
    }

    return 0;
}

static int _sendmsg_processor(evutil_socket_t fd, struct _sendmsg_info *ri)
{
    ri->result = sendmsg(fd, ri->message, ri->flags);
    if (ri->result == -1)
    {
        if (FIL_IS_EAGAIN(errno))
        {
            return -1;
        }

        ri->errn = errno;
    }

    return 0;
}

static void _iothread_event_cb(evutil_socket_t fd, short what, void *arg)
{
    struct _event_cb_info *ecbi = (struct _event_cb_info *)arg;
    PyFilIOThread *iothr;

    pthread_mutex_lock(&(ecbi->ecbi_lock));

    assert(!(ecbi->flags & IOTHR_ECBI_FLAGS_DONE));

    if (!(ecbi->flags & IOTHR_ECBI_FLAGS_WAITING))
    {
        /* Waiter is only waiting us to signal that we've run so it
         * can clean up
         */
        ecbi->flags |= IOTHR_ECBI_FLAGS_DONE;
        event_del(ecbi->event);
        pthread_cond_signal(&(ecbi->ecbi_cond));
        pthread_mutex_unlock(&(ecbi->ecbi_lock));
        return;
    }

    if (what & EV_TIMEOUT)
    {
        ecbi->flags |= IOTHR_ECBI_FLAGS_TIMEOUT|IOTHR_ECBI_FLAGS_DONE;
        event_del(ecbi->event);

        /* NOTE: We need to acquire the GIL here so we can safely run
         * python code inside of fil_waiter_signal().
         *
         * Also: 'ecbi' may be freed by the caller in the middle of the
         * call to PyEval_SaveThread(). So, we need to make sure to save
         * a reference to 'iothr'.
         */
        iothr = ecbi->iothr;

        PyEval_RestoreThread(iothr->thread_state);

        pthread_mutex_unlock(&(ecbi->ecbi_lock));

        fil_waiter_signal(ecbi->waiter);

        iothr->thread_state = PyEval_SaveThread();

        return;
    }

    /* NOTE: Yes, we're keeping the lock while we do the I/O call.  This
     * is generally bad practice, but the I/O call should not block and
     * the lock is specific to this event and only locked after the
     * waiting greenlet becomes active again.  That can only occur if we
     * have a race with an exception in that greenlet that caused it to
     * be scheduled early.
     */
    if ((ecbi->processor != NULL) &&
        (ecbi->processor(fd, ecbi->processor_arg) != 0))
    {
        /* continue to poll */
        pthread_mutex_unlock(&(ecbi->ecbi_lock));
        return;
    }

    ecbi->flags |= IOTHR_ECBI_FLAGS_DONE;

    event_del(ecbi->event);

    /* NOTE: We need to acquire the GIL here so we can safely run
     * python code inside of fil_waiter_signal().
     *
     * Also: 'ecbi' may be freed by the caller in the middle of the
     * call to PyEval_SaveThread(). So, we need to make sure to save
     * a reference to 'iothr'.
     */
    iothr = ecbi->iothr;

    PyEval_RestoreThread(iothr->thread_state);

    pthread_mutex_unlock(&(ecbi->ecbi_lock));

    fil_waiter_signal(ecbi->waiter);

    iothr->thread_state = PyEval_SaveThread();
}

static void _iothread_wakeup_cb(evutil_socket_t fd, short what, void *arg)
{
    (void)fd;
    (void)what;
    (void)arg;
}

static void *_iothread_loop(PyFilIOThread *self)
{
    PyGILState_STATE gstate;
    gstate = PyGILState_Ensure();

    /* NOTE(comstud): We mostly run outside of the GIL, but callbacks
     * may need to block threads to call python code.
     */
    self->thread_state = PyEval_SaveThread();

    for(;;)
    {
        event_base_loop(self->event_base, EVLOOP_ONCE);
        if (self->flags & FIL_IOTHR_FLAGS_SHUTDOWN)
            break;
    }

    PyEval_RestoreThread(self->thread_state);
    PyGILState_Release(gstate);

    return NULL;
}

static void _iothread_wakeup(PyFilIOThread *self)
{
    event_active(self->interrupt_event, 0, 0);
}

static PyFilIOThread *_iothread_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    return (PyFilIOThread *)type->tp_alloc(type, 0);
}

static int _iothread_init(PyFilIOThread *self, PyObject *args, PyObject *kargs)
{
    struct timeval tv;
    int err;

    tv.tv_sec = 60;
    tv.tv_usec = 0;

    self->event_base = event_base_new();
    if (self->event_base == NULL)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "Couldn't create new event_base");
        return -1;
    }

    self->interrupt_event = event_new(self->event_base, -1,
                                      EV_PERSIST, _iothread_wakeup_cb,
                                      NULL);
    if (self->interrupt_event == NULL)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "Couldn't create interrupt event");
        return -1;
    }

    if (event_add(self->interrupt_event, &tv) < 0)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "Couldn't add interrupt event");
        event_free(self->interrupt_event);
        self->interrupt_event = NULL;
        return -1;
    }

    err = pthread_create(&(self->thr_id), NULL,
                         (void *(*)(void *))_iothread_loop, self);
    if (err < 0)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "Couldn't create new event thread");
        return -1;
    }

    self->flags |= FIL_IOTHR_FLAGS_RUNNING;

    return 0;
}

static void _iothread_dealloc(PyFilIOThread *self)
{
    if (self->flags & FIL_IOTHR_FLAGS_RUNNING)
    {
        self->flags |= FIL_IOTHR_FLAGS_SHUTDOWN;
        _iothread_wakeup(self);
        pthread_join(self->thr_id, NULL);
        self->flags &= ~(FIL_IOTHR_FLAGS_RUNNING|FIL_IOTHR_FLAGS_SHUTDOWN);
    }

    if (self->interrupt_event != NULL)
    {
        event_del(self->interrupt_event);
        event_free(self->interrupt_event);
    }

    if (self->event_base != NULL)
    {
        event_base_free(self->event_base);
    }

    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyMethodDef _iothread_methods[] = {
    { NULL, NULL }
};

static PyTypeObject _iothread_type = {
    PyVarObject_HEAD_INIT(0, 0)                 /* Must fill in type value later */
    "filament.iothread.IOThread",               /* tp_name */
    sizeof(PyFilIOThread),                      /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_iothread_dealloc,              /* tp_dealloc */
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
    _iothread_methods,                          /* tp_methods */
    0,                                          /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_iothread_init,                   /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_iothread_new,                     /* tp_new */
    PyObject_Del,                               /* tp_free */
};


/****************/

static int _iothread_process(PyFilIOThread *iothr, int fd, short event,
                             struct _event_cb_info *ecbi,
                             struct timespec *timeout,
                             PyObject *timeout_exc)
{
    struct event *ev;
    FilWaiter *waiter;
    PyThreadState *ts;
    struct timeval tv_buf;
    struct timeval *tv = NULL;
    int err;

    /* TODO(comstud): Can optimize this by not polling if we're in a Thread
     * that doesn't have any filaments
     */
    waiter = fil_waiter_alloc();
    if (waiter == NULL)
    {
        return -1;
    }

    ts = PyEval_SaveThread();

    ecbi->iothr = iothr;

    if (timeout != NULL)
    {
        struct timeval now;
        int usec = timeout->tv_nsec / 1000;

        /* Need to convert absolute time to relative time */

        gettimeofday(&now, NULL);
        tv = &tv_buf;

        if (usec < now.tv_usec)
        {
            tv->tv_usec = 1000000 + usec - now.tv_usec;
            now.tv_sec += 1;
        }
        else
        {
            tv->tv_usec = usec - now.tv_usec;
        }

        if (timeout->tv_sec < now.tv_sec)
        {
            tv->tv_sec = 0;
            tv->tv_usec = 0;
        }
        else
        {
            tv->tv_sec = timeout->tv_sec - now.tv_sec;
        }
    }

    ev = event_new(iothr->event_base, fd, event, _iothread_event_cb, ecbi);
    if (ev == NULL)
    {
        PyEval_RestoreThread(ts);

        fil_waiter_decref(waiter);
        /* FIXME(comstud): Better exception? */
        PyErr_SetString(PyExc_RuntimeError,
                        "Couldn't add new libevent event");
        return -1;
    }

    pthread_mutex_init(&(ecbi->ecbi_lock), NULL);
    pthread_cond_init(&(ecbi->ecbi_cond), NULL);
    ecbi->waiter = waiter;
    ecbi->flags = IOTHR_ECBI_FLAGS_WAITING;
    ecbi->event = ev;

    if (event_add(ev, tv))
    {
        int errno_save = errno;

        pthread_mutex_destroy(&(ecbi->ecbi_lock));
        pthread_cond_destroy(&(ecbi->ecbi_cond));

        PyEval_RestoreThread(ts);

        fil_waiter_decref(waiter);

        PyErr_Format(PyExc_RuntimeError, "Couldn't add event: %d", errno_save);
        return -1;
    }

    PyEval_RestoreThread(ts);

    err = fil_waiter_wait(waiter, NULL, timeout_exc);

    ts = PyEval_SaveThread();

    pthread_mutex_lock(&(ecbi->ecbi_lock));

    if (!(ecbi->flags & IOTHR_ECBI_FLAGS_DONE))
    {
        /* hrmph.. must have received a signal
         * or something else that caused fil_waiter_wait
         * to return early.
         */

        ecbi->flags &= ~IOTHR_ECBI_FLAGS_WAITING;

        /* The event is still scheduled.  Make it active so it can clean up */
        event_active(ecbi->event, 0, 0);

        while(!(ecbi->flags & IOTHR_ECBI_FLAGS_DONE))
        {
            pthread_cond_wait(&(ecbi->ecbi_cond), &(ecbi->ecbi_lock));
        }

        pthread_mutex_unlock(&(ecbi->ecbi_lock));
        pthread_mutex_destroy(&(ecbi->ecbi_lock));
        pthread_cond_destroy(&(ecbi->ecbi_cond));

        PyEval_RestoreThread(ts);

        if (err == 0)
        {
            /* should not happen */
            PyErr_SetString(PyExc_RuntimeError, "waiter returned early with success but i/o not done");
            return -1;
        }
        return -1;
    }

    pthread_mutex_unlock(&(ecbi->ecbi_lock));
    pthread_mutex_destroy(&(ecbi->ecbi_lock));
    pthread_cond_destroy(&(ecbi->ecbi_cond));
    PyEval_RestoreThread(ts);
    fil_waiter_decref(waiter);

    if (ecbi->flags & IOTHR_ECBI_FLAGS_TIMEOUT && !err)
    {
        fil_set_timeout_exc(timeout_exc);
        err = 1;
    }

    /* need to propogate any errors from fil_waiter_wait */
    return err;
}

static void _event_log_cb(int severity, const char *msg)
{
    (void)0;
}

PyFilIOThread *fil_iothread_get(void)
{
    if (_IOThreadObj == NULL)
    {
        PyFilIOThread *self;

        self = (PyFilIOThread *)_iothread_new(&_iothread_type, NULL, NULL);
        if (self == NULL)
        {
            return NULL;
        }

        if (_iothread_init(self, NULL, NULL) < 0)
        {
            Py_DECREF(self);
            return NULL;
        }

        _IOThreadObj = self;
    }

    Py_INCREF(_IOThreadObj);
    return _IOThreadObj;
}

int fil_iothread_read_ready(PyFilIOThread *iothr, int fd,
                            struct timespec *timeout,
                            PyObject *timeout_exc)
{
    struct _event_cb_info *ecbi;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->processor = NULL;
    err = _iothread_process(iothr, fd, EV_READ, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        free(ecbi);
        return 0;
    }

    free(ecbi);
    return -1;
}

int fil_iothread_write_ready(PyFilIOThread *iothr, int fd,
                            struct timespec *timeout,
                            PyObject *timeout_exc)
{
    struct _event_cb_info *ecbi;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->processor = NULL;
    err = _iothread_process(iothr, fd, EV_WRITE, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        free(ecbi);
        return 0;
    }

    free(ecbi);
    return -1;
}

ssize_t fil_iothread_read(PyFilIOThread *iothr, int fd, void *buffer,
                          size_t buf_sz, struct timespec *timeout,
                            PyObject *timeout_exc)
{
    struct _event_cb_info *ecbi;
    ssize_t result;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->read_info.buffer = buffer;
    ecbi->read_info.buf_sz = buf_sz;
    ecbi->processor = (event_processor_t)_read_processor;
    ecbi->processor_arg = &(ecbi->read_info);

    err = _iothread_process(iothr, fd, EV_READ, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        err = ecbi->read_info.errn;
        result = ecbi->read_info.result;
        free(ecbi);

        if (result == -1)
            errno = err;
        return result;
    }

    free(ecbi);
    return -1;
}

ssize_t fil_iothread_write(PyFilIOThread *iothr, int fd, void *buffer,
                           size_t buf_sz, struct timespec *timeout,
                           PyObject *timeout_exc)
{
    struct _event_cb_info *ecbi;
    ssize_t result;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->write_info.buffer = buffer;
    ecbi->write_info.buf_sz = buf_sz;
    ecbi->processor = (event_processor_t)_write_processor;
    ecbi->processor_arg = &(ecbi->write_info);

    err = _iothread_process(iothr, fd, EV_WRITE, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        err = ecbi->write_info.errn;
        result = ecbi->write_info.result;
        free(ecbi);

        if (result == -1)
            errno = err;
        return result;
    }

    free(ecbi);
    return -1;
}

int fil_iothread_accept(PyFilIOThread *iothr, int fd,
                        struct sockaddr *address, socklen_t *address_len,
                        struct timespec *timeout,
                        PyObject *timeout_exc)
{
    struct _event_cb_info *ecbi;
    int result;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->processor = (event_processor_t)_accept_processor;
    ecbi->processor_arg = &(ecbi->accept_info);

    err = _iothread_process(iothr, fd, EV_WRITE, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        err = ecbi->accept_info.errn;
        result = ecbi->accept_info.result;
        free(ecbi);

        if (result == -1)
            errno = err;
        return result;
    }

    free(ecbi);
    return -1;
}

int fil_iothread_connect(PyFilIOThread *iothr, int fd,
                         struct sockaddr *address, socklen_t address_len,
                         struct timespec *timeout,
                         PyObject *timeout_exc)
{
    struct _event_cb_info *ecbi;
    int result;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->processor = (event_processor_t)_connect_processor;
    ecbi->processor_arg = &(ecbi->connect_info);

    err = _iothread_process(iothr, fd, EV_WRITE, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        err = ecbi->connect_info.errn;
        result = ecbi->connect_info.result;
        free(ecbi);

        if (result == -1)
            errno = err;
        return result;
    }

    free(ecbi);
    return -1;
}

ssize_t fil_iothread_recv(PyFilIOThread *iothr, int fd, void *buffer,
                          size_t buf_sz, int flags,
                          struct timespec *timeout,
                          PyObject *timeout_exc)
{
    struct _event_cb_info *ecbi;
    ssize_t result;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->recv_info.buffer = buffer;
    ecbi->recv_info.buf_sz = buf_sz;
    ecbi->recv_info.flags = flags;
    ecbi->processor = (event_processor_t)_recv_processor;
    ecbi->processor_arg = &(ecbi->recv_info);

    err = _iothread_process(iothr, fd, EV_READ, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        err = ecbi->recv_info.errn;
        result = ecbi->recv_info.result;
        free(ecbi);

        if (result == -1)
            errno = err;
        return result;
    }

    free(ecbi);
    return -1;
}

ssize_t fil_iothread_recvfrom(PyFilIOThread *iothr, int fd, void *buffer,
                              size_t buf_sz, int flags,
                              struct sockaddr *address,
                              socklen_t *address_len,
                              struct timespec *timeout,
                              PyObject *timeout_exc)
{
    struct _event_cb_info *ecbi;
    ssize_t result;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->recvfrom_info.buffer = buffer;
    ecbi->recvfrom_info.buf_sz = buf_sz;
    ecbi->recvfrom_info.flags = flags;
    ecbi->recvfrom_info.address = address;
    ecbi->recvfrom_info.address_len = address_len;
    ecbi->processor = (event_processor_t)_recvfrom_processor;
    ecbi->processor_arg = &(ecbi->recvfrom_info);

    err = _iothread_process(iothr, fd, EV_READ, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        err = ecbi->recvfrom_info.errn;
        result = ecbi->recvfrom_info.result;
        free(ecbi);

        if (result == -1)
            errno = err;
        return result;
    }

    free(ecbi);
    return -1;
}

ssize_t fil_iothread_recvmsg(PyFilIOThread *iothr, int fd,
                             struct msghdr *message, int flags,
                             struct timespec *timeout,
                             PyObject *timeout_exc)
{
    struct _event_cb_info *ecbi;
    ssize_t result;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->recvmsg_info.message = message;
    ecbi->recvmsg_info.flags = flags;
    ecbi->processor = (event_processor_t)_recvmsg_processor;
    ecbi->processor_arg = &(ecbi->recvmsg_info);

    err = _iothread_process(iothr, fd, EV_READ, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        err = ecbi->recvmsg_info.errn;
        result = ecbi->recvmsg_info.result;
        free(ecbi);

        if (result == -1)
            errno = err;
        return result;
    }

    free(ecbi);
    return -1;
}

ssize_t fil_iothread_send(PyFilIOThread *iothr, int fd, void *buffer,
                          size_t buf_sz, int flags,
                          struct timespec *timeout,
                          PyObject *timeout_exc)
{
    struct _event_cb_info *ecbi;
    ssize_t result;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->send_info.buffer = buffer;
    ecbi->send_info.buf_sz = buf_sz;
    ecbi->send_info.flags = flags;
    ecbi->processor = (event_processor_t)_send_processor;
    ecbi->processor_arg = &(ecbi->send_info);

    err = _iothread_process(iothr, fd, EV_WRITE, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        err = ecbi->send_info.errn;
        result = ecbi->send_info.result;
        free(ecbi);

        if (result == -1)
            errno = err;
        return result;
    }

    free(ecbi);
    return -1;
}

ssize_t fil_iothread_sendto(PyFilIOThread *iothr, int fd, void *buffer,
                            size_t buf_sz, int flags,
                            struct sockaddr *address,
                            socklen_t address_len,
                            struct timespec *timeout,
                            PyObject *timeout_exc)

{
    struct _event_cb_info *ecbi;
    ssize_t result;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->sendto_info.buffer = buffer;
    ecbi->sendto_info.buf_sz = buf_sz;
    ecbi->sendto_info.flags = flags;
    ecbi->sendto_info.address = address;
    ecbi->sendto_info.address_len = address_len;
    ecbi->processor = (event_processor_t)_sendto_processor;
    ecbi->processor_arg = &(ecbi->sendto_info);

    err = _iothread_process(iothr, fd, EV_WRITE, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        err = ecbi->sendto_info.errn;
        result = ecbi->sendto_info.result;
        free(ecbi);

        if (result == -1)
            errno = err;
        return result;
    }

    free(ecbi);
    return -1;
}

ssize_t fil_iothread_sendmsg(PyFilIOThread *iothr, int fd,
                             struct msghdr *message,
                             int flags, struct timespec *timeout,
                             PyObject *timeout_exc)
{
    struct _event_cb_info *ecbi;
    ssize_t result;
    int err;

    ecbi = malloc(sizeof(*ecbi));
    if (ecbi == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }

    ecbi->sendmsg_info.message = message;
    ecbi->sendmsg_info.flags = flags;
    ecbi->processor = (event_processor_t)_sendmsg_processor;
    ecbi->processor_arg = &(ecbi->sendmsg_info);

    err = _iothread_process(iothr, fd, EV_WRITE, ecbi, timeout, timeout_exc);
    if (err == 0)
    {
        err = ecbi->sendmsg_info.errn;
        result = ecbi->sendmsg_info.result;
        free(ecbi);

        if (result == -1)
            errno = err;
        return result;
    }

    free(ecbi);
    return -1;
}

int fil_iothread_init(PyObject *module)
{
    PyFilCore_Import();
    PyEval_InitThreads();

    evthread_use_pthreads();
    event_set_log_callback(_event_log_cb);

    if (PyType_Ready(&_iothread_type) < 0)
    {
        return -1;
    }

    Py_INCREF((PyObject *)&_iothread_type);
    if (PyModule_AddObject(module, "IOThread",
                           (PyObject *)&_iothread_type) != 0)
    {
        Py_DECREF((PyObject *)&_iothread_type);
        return -1;
    }

    PyFilIOThread_Type = &_iothread_type;

    FIL_COPY_IO_API();

    return 0;
}
