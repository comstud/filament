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

/*
 * SYNCHRONIZATION INVARIANT for the io-thread <-> waiting-greenthread handoff.
 * This is the crux of the whole io layer's thread-safety; keep it intact.
 *
 * Two actors touch a single heap-allocated 'ecbi' (and the FilWaiter it points
 * at): this callback, which runs on the io thread, and _iothread_process(),
 * which runs on the thread that is blocked in fil_waiter_wait(). Ownership of
 * the memory is fixed: the ORIGINATING call (fil_iothread_read/recv/... ->
 * _iothread_process) allocates 'ecbi' and is the ONLY one that frees it (and it
 * calls fil_waiter_decref() to release the waiter). The io thread NEVER frees
 * either object.
 *
 * This callback runs entirely WITHOUT the GIL. fil_waiter_signal() performs no
 * Python API calls for io waiters (they are always untimed), so the io thread
 * never has to bounce the GIL per completion -- that GIL ping-pong between the
 * io thread and the scheduler thread used to dominate socket-heavy workloads.
 * What guards the memory instead is a happens-before chain built from plain
 * mutexes:
 *
 *   (a) 'ecbi_lock' + the IOTHR_ECBI_FLAGS_DONE flag serialize the two actors
 *       around the syscall/result. The waiter side re-locks ecbi_lock after
 *       waking (in _iothread_process) and only then inspects DONE / frees; that
 *       lock acquire happens-after this callback's unlock, so everything the io
 *       thread wrote to 'ecbi' before the unlock is visible and settled.
 *
 *   (b) The io thread's LAST touches of 'ecbi' -- including the
 *       fil_waiter_signal() call on ecbi->waiter -- happen while it still
 *       holds ecbi_lock. The waiter side cannot return from fil_waiter_wait()
 *       until the signal has enqueued its wakeup (which happens inside that
 *       critical section), and it must then re-acquire ecbi_lock before it
 *       frees anything, so the free is ordered strictly AFTER the io thread
 *       is done with 'ecbi'.
 *
 *   (c) The FilWaiter itself: fil_waiter_signal()'s last touch of the waiter
 *       happens under waiter->waiter_lock, and the waiter side re-acquires
 *       that lock after resuming, before fil_waiter_decref() can free it
 *       (see the barrier in fil_waiter_wait()).
 *
 * Corollary: waiter->refcnt stays non-atomic and is only ever mutated under the
 * GIL on the owning scheduler thread; the io thread never touches it.
 */
static void _iothread_event_cb(evutil_socket_t fd, short what, void *arg)
{
    struct _event_cb_info *ecbi = (struct _event_cb_info *)arg;

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

        /* GIL-free wakeup; see invariant comment (b)/(c) above. */
        fil_waiter_signal_nogil(ecbi->waiter);

        pthread_mutex_unlock(&(ecbi->ecbi_lock));

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

    /* GIL-free wakeup; see invariant comment (b)/(c) above. */
    fil_waiter_signal_nogil(ecbi->waiter);

    pthread_mutex_unlock(&(ecbi->ecbi_lock));
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

    /* Shutdown-only exit (driven by _iothread_atexit during interpreter
     * finalization). We deliberately do NOT PyEval_RestoreThread() /
     * PyGILState_Release() here: the GIL machinery is already being torn down
     * by Py_FinalizeEx(), so touching it would crash. We hold no GIL at this
     * point (all callbacks release it before event_base_loop() returns), so it
     * is safe to just unwind. The leaked gstate/thread_state is irrelevant at
     * process teardown. */
    (void)gstate;
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

/*
 * Shutdown-lifecycle fix.
 *
 * The io thread lives on a leaked static singleton (_IOThreadObj), so
 * _iothread_dealloc() (which would join the thread) never runs. Without this
 * hook the io thread is still spinning in event_base_loop() when Py_Finalize()
 * begins tearing the interpreter down; the next time a callback grabs the GIL
 * (PyEval_RestoreThread) or touches interpreter/thread state it operates on
 * freed memory and corrupts the heap at exit.
 *
 * We register this with Py_AtExit(). By the time a C-level Py_AtExit handler
 * runs, CPython has already torn the per-thread state down far enough that GIL
 * primitives (PyEval_SaveThread / PyGILState_*) are no longer usable here, so
 * this path performs NO Python/GIL calls at all: it only pokes libevent (the
 * interrupt event, which is pure C and thread-safe under evthread_use_pthreads)
 * and joins the pthread. Correspondingly, the io loop's shutdown exit
 * (see _iothread_loop) must NOT re-acquire the GIL either. At this point the
 * program's work is finished, so the io thread is idle inside event_base_loop()
 * with no callback in flight; waking it and joining is deadlock-free without
 * touching the GIL.
 */
static void _iothread_atexit(void)
{
    PyFilIOThread *self = _IOThreadObj;

    if (self == NULL || !(self->flags & FIL_IOTHR_FLAGS_RUNNING))
    {
        return;
    }

    self->flags |= FIL_IOTHR_FLAGS_SHUTDOWN;
    _iothread_wakeup(self);

    pthread_join(self->thr_id, NULL);

    self->flags &= ~(FIL_IOTHR_FLAGS_RUNNING | FIL_IOTHR_FLAGS_SHUTDOWN);
}

/*
 * Python-level atexit shutdown (primary mechanism).
 *
 * The C-level Py_AtExit hook above runs from call_ll_exitfuncs(), very late in
 * Py_Finalize().  On Python 3 that is safe (the GIL has already been released by
 * then).  On Python 2, however, call_ll_exitfuncs() still runs on the main
 * thread while it holds the GIL *lock* but with its thread state already swapped
 * to NULL.  If the io thread happens to be mid-flight in an fd callback
 * (_iothread_event_cb -> PyEval_RestoreThread) it blocks forever waiting for
 * that GIL lock, and the main thread's pthread_join() deadlocks against it --
 * and we cannot drop the lock cleanly there because there is no thread state to
 * hand to the GIL machinery.
 *
 * So we ALSO register a shutdown via the Python 'atexit' module (run from
 * call_py_exitfuncs(), early in finalization, while both the GIL and the main
 * thread state are fully valid).  There we can drop the GIL the normal way
 * (Py_BEGIN_ALLOW_THREADS) around the join, letting any in-flight io callback
 * acquire the GIL, complete, and let the loop observe the shutdown flag.  Once
 * this has run, FIL_IOTHR_FLAGS_RUNNING is clear, so the later C-level
 * _iothread_atexit() becomes a no-op.  This makes shutdown deadlock-free and
 * behaves identically on Python 2 and 3.
 */
static PyObject *_iothread_atexit_py(PyObject *self, PyObject *ignored)
{
    PyFilIOThread *iothr = _IOThreadObj;

    (void)self;
    (void)ignored;

    if (iothr == NULL || !(iothr->flags & FIL_IOTHR_FLAGS_RUNNING))
    {
        Py_RETURN_NONE;
    }

    iothr->flags |= FIL_IOTHR_FLAGS_SHUTDOWN;
    _iothread_wakeup(iothr);

    Py_BEGIN_ALLOW_THREADS
    pthread_join(iothr->thr_id, NULL);
    Py_END_ALLOW_THREADS

    iothr->flags &= ~(FIL_IOTHR_FLAGS_RUNNING | FIL_IOTHR_FLAGS_SHUTDOWN);
    Py_RETURN_NONE;
}

static PyMethodDef _iothread_atexit_py_def = {
    "_fil_iothread_shutdown", (PyCFunction)_iothread_atexit_py,
    METH_NOARGS, NULL
};

/* Register _iothread_atexit_py() with the Python 'atexit' module.  Called once,
 * when the io thread singleton is first created, with the GIL held. */
static int _iothread_register_py_atexit(void)
{
    PyObject *cb;
    PyObject *atexit_mod;
    PyObject *res;

    cb = PyCFunction_NewEx(&_iothread_atexit_py_def, NULL, NULL);
    if (cb == NULL)
    {
        return -1;
    }

    atexit_mod = PyImport_ImportModule("atexit");
    if (atexit_mod == NULL)
    {
        Py_DECREF(cb);
        return -1;
    }

    res = PyObject_CallMethod(atexit_mod, "register", "O", cb);
    Py_DECREF(atexit_mod);
    Py_DECREF(cb);
    if (res == NULL)
    {
        return -1;
    }
    Py_DECREF(res);
    return 0;
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

    /* Respect tp_free: Python subclass instances are GC-allocated, and
     * PyObject_Del on them frees the wrong pointer (heap corruption). */
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyMethodDef _iothread_methods[] = {
    { NULL, NULL }
};

static PyTypeObject _iothread_type = {
    PyVarObject_HEAD_INIT(0, 0)                 /* Must fill in type value later */
    "_filament.io.IOThread",                    /* tp_name */
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
    FIL_DEFAULT_TPFLAGS,                        /* tp_flags */
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

    /* NOTE: we deliberately KEEP the GIL through the (fast, non-blocking)
     * event setup below. The io thread's callback never takes the GIL (see
     * _iothread_event_cb), so holding it here cannot deadlock, and dropping
     * and re-taking it per operation just added two more GIL handoffs to
     * every blocking socket call.
     */

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

        fil_waiter_decref(waiter);

        PyErr_Format(PyExc_RuntimeError, "Couldn't add event: %d", errno_save);
        return -1;
    }

    err = fil_waiter_wait(waiter, NULL, timeout_exc);

    pthread_mutex_lock(&(ecbi->ecbi_lock));

    if (!(ecbi->flags & IOTHR_ECBI_FLAGS_DONE))
    {
        /* hrmph.. must have received a signal
         * or something else that caused fil_waiter_wait
         * to return early.
         */

        ecbi->flags &= ~IOTHR_ECBI_FLAGS_WAITING;

        /* The event is still scheduled.  Make it active so it can clean up.
         * The io callback needs no GIL, so we can wait for it while holding
         * the GIL without risk of deadlock (this is a rare cancellation
         * path; the wait is bounded by one io-thread dispatch). */
        event_active(ecbi->event, 0, 0);

        while(!(ecbi->flags & IOTHR_ECBI_FLAGS_DONE))
        {
            ts = PyEval_SaveThread();
            pthread_cond_wait(&(ecbi->ecbi_cond), &(ecbi->ecbi_lock));
            PyEval_RestoreThread(ts);
        }

        pthread_mutex_unlock(&(ecbi->ecbi_lock));
        pthread_mutex_destroy(&(ecbi->ecbi_lock));
        pthread_cond_destroy(&(ecbi->ecbi_cond));

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
    fil_waiter_decref(waiter);

    if (ecbi->flags & IOTHR_ECBI_FLAGS_TIMEOUT && !err)
    {
        fil_set_timeout_exc(timeout_exc);
        err = 1;
    }

    /* need to propogate any errors from fil_waiter_wait */
    return err;
}

/*
 * ---------------------------------------------------------------------------
 * Cached edge-triggered fd-readiness waits.
 *
 * The classic path above pays, for EVERY blocked operation: an ecbi malloc,
 * two mutex/cond init+destroy pairs, an event_new/event_free, and -- worst of
 * all -- an event_add and an event_del, i.e. two epoll_ctl syscalls plus
 * event_base locking and (often) a notify write to wake the io thread.
 *
 * For the overwhelmingly common case (a socket blocking with no timeout), we
 * instead keep ONE persistent edge-triggered libevent event per (socket,
 * direction), owned by the socket object and registered on first use for the
 * lifetime of the fd.  Blocked operations then cost only: park the greenlet,
 * let the io thread's epoll report the edge, signal the waiter (GIL-free).
 * No epoll_ctl, no allocations beyond the waiter itself.
 *
 * Why edge-triggered is safe here: callers ALWAYS attempt the (non-blocking)
 * syscall first and only wait after EAGAIN, i.e. they have drained the fd to
 * "not ready" before parking, so every future readiness transition generates
 * an edge.  Edges that arrive while nobody is waiting are LATCHED in 'ready'
 * and consumed by the next waiter ("one free retry"), so no wakeup is ever
 * lost.  (epoll also reports an initial edge at EPOLL_CTL_ADD time if the fd
 * is already ready, covering the registration race.)
 *
 * Locking: 'lock' guards all fields; the io thread's callback takes it, and
 * fil_waiter_signal() is called while holding it, which (via the waiter_lock
 * barrier in fil_waiter_wait) orders the io thread's last touch of the
 * waiter strictly before the woken side can free it.  Lock order here is
 * fdwait->lock -> waiter_lock -> sched_lock; nothing takes them in reverse.
 *
 * Lifecycle: the owner (fil_socket.c) calls fil_iothread_fdwait_destroy()
 * when the fd is closed/detached/replaced.  If a waiter is parked at that
 * moment (another greenlet closing the socket out from under a blocked one),
 * the waiter is woken (it will retry its syscall and get EBADF -- better
 * than the classic path, which would simply hang) and inherits the duty of
 * freeing the orphaned struct.
 * ---------------------------------------------------------------------------
 */
struct _fil_io_fdwait
{
    pthread_mutex_t lock;
    struct event *ev;      /* persistent EV_ET event; NULL until first use */
    FilWaiter *waiter;     /* currently parked waiter, if any */
    /* Count of readiness edges seen so far, bumped by the io callback on
     * every fire. Callers snapshot this (fil_iothread_fdwait_seq) BEFORE
     * their non-blocking syscall; wait_cached refuses to park -- returning
     * "retry the syscall" instead -- if an edge fired since the snapshot.
     * That closes every "edge landed between my EAGAIN and my park" window,
     * including the multi-reader one where the edge was consumed by ANOTHER
     * waiter's wakeup and thus latched nothing. */
    unsigned int edge_seq;
    int busy;              /* a wait_cached caller is between park and its
                              post-wake bookkeeping; defers a concurrent
                              destroy's free to that caller */
    int orphaned;          /* owner destroyed us while busy/parked */
};

static void _iothread_fdwait_event_cb(evutil_socket_t fd, short what, void *arg)
{
    FilIOFDWait *fdw = (FilIOFDWait *)arg;
    FilWaiter *waiter;

    (void)fd;
    (void)what;

    pthread_mutex_lock(&(fdw->lock));
    fdw->edge_seq++;
    waiter = fdw->waiter;
    if (waiter != NULL)
    {
        fdw->waiter = NULL;
        /* GIL-free untimed wakeup; safe while holding fdw->lock (see the
         * locking notes above). */
        fil_waiter_signal_nogil(waiter);
    }
    pthread_mutex_unlock(&(fdw->lock));
}

/*
 * Snapshot the edge counter for a (possibly still NULL) cached fd-waiter.
 * Callers take this BEFORE their non-blocking syscall and hand it to
 * fil_iothread_wait_cached(). Lock-free read: a torn/stale read can only
 * cause one extra (benign) syscall retry, never a missed wakeup, because
 * wait_cached re-checks under the lock.
 */
unsigned int fil_iothread_fdwait_seq(FilIOFDWait *fdw)
{
    if (fdw == NULL)
    {
        return 0;
    }
    return fdw->edge_seq;
}

static void _iothread_fdwait_free(FilIOFDWait *fdw)
{
    pthread_mutex_destroy(&(fdw->lock));
    free(fdw);
}

/*
 * Wait (cooperatively) until 'fd' sees a readiness edge for the requested
 * direction.  Call ONLY after the non-blocking syscall returned EAGAIN, and
 * only with a 'seq' snapshot (fil_iothread_fdwait_seq) taken BEFORE that
 * syscall.
 *
 * Returns:
 *   0  -- an edge has fired since 'seq' was snapshotted (possibly while we
 *         checked); retry the syscall
 *   1  -- cached path unavailable (no ET support / another waiter already
 *         parked on this direction); caller must fall back to the classic
 *         fil_iothread_*_ready path
 *   -1 -- error (Python exception set)
 */
int fil_iothread_wait_cached(PyFilIOThread *iothr, FilIOFDWait **cachep, int fd, int for_write, unsigned int seq)
{
    FilIOFDWait *fdw = *cachep;
    FilWaiter *waiter;
    int err;
    int orphaned;

    if (fdw == NULL)
    {
        if (!(event_base_get_features(iothr->event_base) & EV_FEATURE_ET))
        {
            /* Backend without edge-trigger support: use the classic path. */
            return 1;
        }

        fdw = calloc(1, sizeof(*fdw));
        if (fdw == NULL)
        {
            PyErr_NoMemory();
            return -1;
        }
        pthread_mutex_init(&(fdw->lock), NULL);
        *cachep = fdw;
    }

    pthread_mutex_lock(&(fdw->lock));

    if (fdw->edge_seq != seq)
    {
        /* An edge fired after the caller's snapshot (i.e. possibly after --
         * and invalidating -- its EAGAIN). Retry the syscall instead of
         * parking; parking here could sleep through data that already
         * arrived (and, in the multi-reader case, was only delivered to a
         * DIFFERENT waiter's wakeup). */
        pthread_mutex_unlock(&(fdw->lock));
        return 0;
    }

    if (fdw->waiter != NULL)
    {
        /* Another greenlet is already parked on this (fd, direction); rare.
         * Let the caller take the classic multi-waiter-capable path. */
        pthread_mutex_unlock(&(fdw->lock));
        return 1;
    }

    if (fdw->ev == NULL)
    {
        fdw->ev = event_new(iothr->event_base, fd,
                            (for_write ? EV_WRITE : EV_READ)|EV_PERSIST|EV_ET,
                            _iothread_fdwait_event_cb, fdw);
        if (fdw->ev == NULL || event_add(fdw->ev, NULL) != 0)
        {
            if (fdw->ev != NULL)
            {
                event_free(fdw->ev);
                fdw->ev = NULL;
            }
            pthread_mutex_unlock(&(fdw->lock));
            PyErr_SetString(PyExc_RuntimeError,
                            "Couldn't add persistent libevent event");
            return -1;
        }
    }

    waiter = fil_waiter_alloc();
    if (waiter == NULL)
    {
        pthread_mutex_unlock(&(fdw->lock));
        return -1;
    }

    fdw->waiter = waiter;
    fdw->busy = 1;
    pthread_mutex_unlock(&(fdw->lock));

    err = fil_waiter_wait(waiter, NULL, NULL);

    pthread_mutex_lock(&(fdw->lock));
    if (err && fdw->waiter == waiter)
    {
        /* Exception resumed us before (or instead of) the io callback;
         * detach so the next edge is latched rather than signaled into a
         * dead waiter. */
        fdw->waiter = NULL;
    }
    fdw->busy = 0;
    orphaned = fdw->orphaned;
    pthread_mutex_unlock(&(fdw->lock));

    fil_waiter_decref(waiter);

    if (orphaned)
    {
        /* The owner destroyed the cache while we were parked (fd closed
         * under us); we inherit the free.  The owner already deleted the
         * libevent event (so no callback can be in flight) and already
         * cleared/replaced *cachep -- do not touch it. */
        _iothread_fdwait_free(fdw);
    }

    return err ? -1 : 0;
}

/*
 * Tear down a cached fd-waiter.  Called by the owner when the fd is closed,
 * detached, or replaced.  Safe to call with cache == NULL.
 */
void fil_iothread_fdwait_destroy(FilIOFDWait *fdw)
{
    struct event *ev;
    FilWaiter *waiter;
    int deferred;

    if (fdw == NULL)
    {
        return;
    }

    /* Detach the event first, WITHOUT holding fdw->lock: event_del blocks
     * until any in-flight callback completes, and the callback takes
     * fdw->lock -- holding it here would deadlock.  Once event_del returns,
     * no callback can touch 'fdw' ever again. */
    pthread_mutex_lock(&(fdw->lock));
    ev = fdw->ev;
    fdw->ev = NULL;
    pthread_mutex_unlock(&(fdw->lock));

    if (ev != NULL)
    {
        event_del(ev);
        event_free(ev);
    }

    pthread_mutex_lock(&(fdw->lock));

    waiter = fdw->waiter;
    if (waiter != NULL)
    {
        /* A greenlet is still parked; wake it (it will retry its syscall on
         * the now-closed fd and surface an error instead of hanging). */
        fdw->waiter = NULL;
    }

    /* If a wait_cached caller is still between park and its post-wake
     * bookkeeping (including the one we may just have detached), it will
     * take fdw->lock again before it is done -- let IT free the struct. */
    deferred = fdw->busy;
    if (deferred)
    {
        fdw->orphaned = 1;
    }

    if (waiter != NULL)
    {
        fil_waiter_signal(waiter);
    }

    pthread_mutex_unlock(&(fdw->lock));

    if (!deferred)
    {
        _iothread_fdwait_free(fdw);
    }
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

        /* Register the Python-level atexit shutdown now that the thread is
         * running.  See _iothread_atexit_py() for why this (rather than only
         * the C-level Py_AtExit hook) is required for a deadlock-free exit on
         * Python 2. */
        if (_iothread_register_py_atexit() < 0)
        {
            /* Non-fatal: the C-level Py_AtExit hook remains as a fallback (it
             * is deadlock-free on Python 3, and on Python 2 the process is
             * exiting regardless).  Clear the error so we don't leave one set
             * on the caller's path. */
            PyErr_Clear();
        }
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

    /* A listening socket signals an incoming connection by becoming
     * READABLE, not writable. Polling EV_WRITE here meant accept() would
     * never be woken and the filament hung forever. */
    err = _iothread_process(iothr, fd, EV_READ, ecbi, timeout, timeout_exc);
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

    /* Ensure the io thread is stopped and joined before the interpreter
     * finalizes (the singleton is leaked so tp_dealloc never runs). */
    if (Py_AtExit(_iothread_atexit) < 0)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "Couldn't register io thread atexit handler");
        return -1;
    }

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
