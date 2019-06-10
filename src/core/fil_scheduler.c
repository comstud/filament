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

/****************/

#define _scheduler_get() \
    (PyFilScheduler *)pthread_getspecific(_scheduler_key)
#define _scheduler_set(__x) \
    pthread_setspecific(_scheduler_key, __x)
static pthread_key_t _scheduler_key = 0;

/****************/

static inline FilSchedEvent *_get_ready_events(FilSchedEventList *elist, struct timespec **next_run_ret)
{
    /* Extract events that we can run out of 'elist' and keep
     * the list as 'cur_events'.
     */
    FilSchedEvent *cur_events = elist->head;

    if (cur_events == NULL)
    {
        *next_run_ret = NULL;
        return NULL;
    }

    struct timespec now;
    FilSchedEvent *event;

    fil_timespec_now(&now);
    for(event=cur_events;event;event=event->next)
        if (FIL_TIMESPEC_COMPARE(&(event->ts), &now, >))
            break;
    if (event == cur_events)
    {
        /* No events ready */
        *next_run_ret = &(event->ts);
        return NULL;
    }

    if (event == NULL)
    {
        /* All events ready */
        elist->head = NULL;
        elist->tail = NULL;
        *next_run_ret = NULL;
    }
    else
    {
        /* 'event' is not ready yet.  Cut the list short here */
        elist->head = event;
        event->prev->next = NULL;
        event->prev = NULL;
        *next_run_ret = &event->ts;
    }

    return cur_events;
}

static void _scheduler_key_delete(void *sched)
{
    _scheduler_set(NULL);
}

static int _scheduler_add_event(PyFilScheduler *sched, struct timespec *ts, uint32_t flags, fil_event_cb_t cb, void *cb_arg)
{
    FilSchedEventList *elist = &sched->events;
    FilSchedEvent *event;

    event = malloc(sizeof(*event));
    if (event == NULL)
    {
        return -1;
    }

    event->flags = flags;
    event->cb = cb;
    event->cb_arg = cb_arg;
    if (ts == NULL)
    {
        event->ts.tv_sec = 0;
        event->ts.tv_nsec = 0;
    }
    else
    {
        event->ts = *ts;
    }

    pthread_mutex_lock(&(sched->sched_lock));

    /* FIXME: Convert to a priority queue */
    if (elist->head == NULL ||
        FIL_EVENT_COMPARE(event, elist->head, <))
    {
        event->prev = NULL;
        if ((event->next = elist->head) == NULL)
            elist->tail = event;
        else
            event->next->prev = event;
        elist->head = event;

        pthread_cond_signal(&(sched->sched_cond));
        pthread_mutex_unlock(&(sched->sched_lock));
        return 0;
    }

    /* See if we can just add to the end of the list */
    if (FIL_EVENT_COMPARE(event, elist->tail, >=))
    {
        event->next = NULL;
        event->prev = elist->tail;
        event->prev->next = event;
        elist->tail = event;

        /* No need to signal as something is before this event */
        pthread_mutex_unlock(&(sched->sched_lock));
        return 0;
    }

    FilSchedEvent *cur_event = elist->head;

    /* Find event we should insert AFTER */
    while (cur_event->next && FIL_EVENT_COMPARE(event, cur_event->next, >=))
    {
        cur_event = cur_event->next;
    }

    event->next = cur_event->next;
    event->prev = cur_event;
    event->next->prev = event;
    cur_event->next = event;

    /* No need to signal as something is before this event */
    pthread_mutex_unlock(&(sched->sched_lock));

    return 0;
}

/***********************************************
************************************************
************************************************
************************************************
************************************************
***********************************************/

static PyGreenlet *_create_greenlet(PyFilScheduler *self)
{
    PyObject *main_method;
    PyGreenlet *greenlet;

    assert(self->greenlet == NULL);
    main_method = PyObject_GetAttrString((PyObject *)self, "main");
    if (main_method == NULL)
        return NULL;
    greenlet = PyGreenlet_New(main_method, NULL);
    Py_DECREF(main_method);
    if (greenlet == NULL)
        return NULL;
    return greenlet;
}

static void _handle_greenlet_done(PyGreenlet **greenlet)
{
    if (*greenlet == NULL)
        return;
    Py_DECREF(*greenlet);
    *greenlet = NULL;
}

static int _greenlet_switch(PyGreenlet *greenlet)
{
    PyObject *result = PyGreenlet_Switch(greenlet, NULL, NULL);
    Py_XDECREF(result);
    return (result == NULL) ? -1 : 0;
}

static void _greenlet_event_switch(PyFilScheduler *sched, PyGreenlet *greenlet)
{
    _greenlet_switch(greenlet);
    Py_DECREF(greenlet);
}

static PyObject *_sched_new(PyTypeObject *type, PyObject *args, PyObject *kw)
{
    PyFilScheduler *self = NULL;

    self = _scheduler_get();
    if (self != NULL)
    {
        /* This could only happen if someone called Scheduler() */
        Py_INCREF(self);
        return (PyObject *)self;
    }

    self = (PyFilScheduler *)type->tp_alloc(type, 0);
    if (self == NULL)
        return NULL;

    pthread_mutex_init(&(self->sched_lock), NULL);
    pthread_cond_init(&(self->sched_cond), NULL);
    self->greenlet = NULL;
    self->thread_state = NULL;
    self->events.head = self->events.tail = NULL;
    self->running = 0;
    self->aborting = 0;
    _scheduler_set(self);
    return (PyObject *)self;
}

static int _sched_init(PyFilScheduler *self, PyObject *args, PyObject *kargs)
{
    if (self->greenlet != NULL)
    {
        return 0;
    }

    self->greenlet = _create_greenlet(self);
    if (self->greenlet == NULL)
    {
        return -1;
    }

    self->system_exceptions = PyTuple_Pack(2, PyExc_SystemError,
                                           PyExc_KeyboardInterrupt);
    if (self->system_exceptions == NULL)
    {
        Py_CLEAR(self->greenlet);
        return -1;
    }

    /* Switch to the scheduler greenlet, but immediately switch back */
    fil_scheduler_gl_switch(self, NULL, self->greenlet->parent);
    if (_greenlet_switch(self->greenlet) < 0)
    {
        Py_CLEAR(self->system_exceptions);
        Py_CLEAR(self->greenlet);
        return -1;
    }

    return 0;
}

static void _sched_dealloc(PyFilScheduler *self)
{
    pthread_mutex_destroy(&(self->sched_lock));
    pthread_cond_destroy(&(self->sched_cond));
    Py_CLEAR(self->system_exceptions);
#if 0
    _handle_greenlet_done(&(sched->greenlet));
#endif
    _scheduler_set(NULL);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

PyDoc_STRVAR(sched_fil_switch_doc, "Schedule a filament to run.");
static PyObject *_sched_fil_switch(PyFilScheduler *self, PyObject *args)
{
    PyGreenlet *greenlet;

    if (!PyArg_ParseTuple(args, "O", (PyObject **)&greenlet))
        return NULL;

    fil_scheduler_gl_switch(self, NULL, greenlet);

    Py_RETURN_NONE;
}

static void _handle_exception(PyFilScheduler *self)
{
    PyObject *exc_type, *val, *tb;

    PyErr_Fetch(&exc_type, &val, &tb);
    if (exc_type == NULL)
        return;

    if (PyErr_GivenExceptionMatches(exc_type,
                                    self->system_exceptions))
    {
        /* 
         * Raise these in our parent. This immediately switches
         * to the parent.
         */
        PyGreenlet_Throw(self->greenlet->parent, exc_type, val, tb);
#if 0
        /* Throw() automatically switches */
        _greenlet_switch(self->greenlet->parent);
#endif
    }

    /* Squash other exceptions */
    fprintf(stderr, "Squashing exc in greenlet: ");
    PyObject_Print(val, stderr, 0);
    fprintf(stderr, "\n");
    Py_DECREF(exc_type);
    Py_XDECREF(val);
    Py_XDECREF(tb);
}

PyDoc_STRVAR(sched_main_doc, "Main entrypoint for the Scheduler greenlet.");
static PyObject *_sched_main(PyFilScheduler *self, PyObject *args)
{
    struct timespec *wait_time;
    FilSchedEvent *event;
    FilSchedEvent *ready_events;
    int err;

    /* Allow other threads to run. */
    self->thread_state = PyEval_SaveThread();

    pthread_mutex_lock(&(self->sched_lock));
    self->running = 1;
    while (!self->aborting)
    {
        ready_events = _get_ready_events(&(self->events),
                                         &wait_time);
        if (ready_events == NULL)
        {
            err = fil_pthread_cond_wait_min(&(self->sched_cond),
                                            &(self->sched_lock),
                                            wait_time);
            if (err == EINTR)
            {
                pthread_mutex_unlock(&(self->sched_lock));
                PyEval_RestoreThread(self->thread_state);
                self->thread_state = NULL;
                if (PyErr_Occurred() != NULL || PyErr_CheckSignals())
                {
                    _handle_exception(self);
                }
                self->thread_state = PyEval_SaveThread();
                pthread_mutex_lock(&(self->sched_lock));
            }

            continue;
        }

        pthread_mutex_unlock(&(self->sched_lock));

        while((event = ready_events) != NULL)
        {
            ready_events = event->next;
            if (event->flags & FIL_SCHED_EVENT_FLAGS_DONTBLOCK_THREADS)
            {
                /* FIXME(comstud): Probably should allow a way for
                 * event callbacks to return a failure that can be
                 * raised back up.
                 */
                event->cb(self, event->cb_arg);
            }
            else
            {
                PyEval_RestoreThread(self->thread_state);
                self->thread_state = NULL;

                event->cb(self, event->cb_arg);

                if (PyErr_Occurred() != NULL || PyErr_CheckSignals())
                {
                    _handle_exception(self);
                }

                self->thread_state = PyEval_SaveThread();
            }
            free(event);
        }

        pthread_mutex_lock(&(self->sched_lock));
    }

    self->running = 0;
    pthread_cond_signal(&(self->sched_cond));
    pthread_mutex_unlock(&(self->sched_lock));

    /* Block threads */
    PyEval_RestoreThread(self->thread_state);
    self->thread_state = NULL;
    Py_RETURN_NONE;
}

PyDoc_STRVAR(sched_abort_doc, "Abort a scheduler.");
static PyObject *_sched_abort(PyFilScheduler *self, PyObject *args)
{
    PyFilScheduler *current_sched;

    if (self->greenlet == NULL)
    {
        PyErr_SetString(PyExc_RuntimeError, "Already aborted");
        return NULL;
    }

    current_sched = _scheduler_get();
    if (current_sched == self)
    {
        /* The scheduler must already be running, but switched out via
         * a callback.  Switching back to it will cause it to exit.
         */
        self->aborting = 1;
        PyObject *result = PyGreenlet_Switch(self->greenlet, NULL, NULL);
        Py_XDECREF(result);
        _handle_greenlet_done(&(self->greenlet));
        Py_RETURN_NONE;
    }

    /* A different thread wants to make our scheduler abort?
     * We probably shouldn't even allow this... but maybe it'll be
     * useful for tests.
     */
    Py_BEGIN_ALLOW_THREADS
    pthread_mutex_lock(&(self->sched_lock));
    self->aborting = 1;
    /* FIXME: don't use the same cond here and below */
    pthread_cond_signal(&(self->sched_cond));
    while (self->running)
    {
        pthread_cond_wait(&(self->sched_cond),
                          &(self->sched_lock));
    }
    pthread_mutex_unlock(&(self->sched_lock));
    Py_END_ALLOW_THREADS
    Py_RETURN_NONE;
}

PyDoc_STRVAR(sched_switch_doc, "Switch to scheduler greenlet.");
static PyObject *_sched_switch(PyFilScheduler *self, PyObject *args)
{
    fil_scheduler_switch(self);
    Py_RETURN_NONE;
}

PyDoc_STRVAR(sched_greenlet_doc, "Return scheduler greenlet.");
static PyObject *_sched_greenlet(PyFilScheduler *self, PyObject *args)
{
    if (self->greenlet == NULL)
    {
        Py_RETURN_NONE;
    }
    Py_INCREF(self->greenlet);
    return (PyObject *)self->greenlet;
}

static PyMethodDef _sched_methods[] = {
    {"fil_switch", (PyCFunction)_sched_fil_switch, METH_VARARGS, sched_fil_switch_doc},
    {"main", (PyCFunction)_sched_main, METH_VARARGS, sched_main_doc},
    {"abort", (PyCFunction)_sched_abort, METH_VARARGS, sched_abort_doc},
    {"switch", (PyCFunction)_sched_switch, METH_NOARGS, sched_switch_doc},
    {"greenlet", (PyCFunction)_sched_greenlet, METH_NOARGS, sched_greenlet_doc},
    { NULL, NULL }
};

static PyTypeObject _scheduler_type = {
    PyVarObject_HEAD_INIT(0, 0)                 /* Must fill in type
                                                   value later */
    "filament.Scheduler",                       /* tp_name */
    sizeof(PyFilScheduler),                     /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)_sched_dealloc,                 /* tp_dealloc */
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
    _sched_methods,                             /* tp_methods */
    0,                                          /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    (initproc)_sched_init,                      /* tp_init */
    PyType_GenericAlloc,                        /* tp_alloc */
    (newfunc)_sched_new,                        /* tp_new */
    PyObject_Del,                               /* tp_free */
};

/****************/

PyFilScheduler *fil_scheduler_get(int create)
{
    PyFilScheduler *self = _scheduler_get();

    if ((self != NULL) || !create)
    {
        return self;
    }

    self = (PyFilScheduler *)_sched_new(&_scheduler_type, NULL, NULL);
    if (self == NULL)
    {
        return NULL;
    }

    if (_sched_init(self, NULL, NULL) < 0)
    {
        Py_DECREF(self);
        return NULL;
    }

    return self;
}

int fil_scheduler_add_event(PyFilScheduler *sched, struct timespec *ts,
                       uint32_t flags, fil_event_cb_t cb, void *cb_arg)
{
   return _scheduler_add_event(sched, ts, flags, cb, cb_arg);
}

int fil_scheduler_switch(PyFilScheduler *sched)
{
    return _greenlet_switch(sched->greenlet);
}

void fil_scheduler_gl_switch(PyFilScheduler *sched, struct timespec *ts, PyGreenlet *greenlet)
{
    Py_INCREF(greenlet);
    fil_scheduler_add_event(sched, ts, 0,
                            (fil_event_cb_t)_greenlet_event_switch,
                            greenlet);
}

PyGreenlet *fil_scheduler_greenlet(PyFilScheduler *sched)
{
    return sched->greenlet;
}

int fil_scheduler_init(PyObject *module, PyFilCore_CAPIObject *capi)
{
    pthread_key_create(&_scheduler_key, _scheduler_key_delete);
    PyGreenlet_Import();

    if (PyType_Ready(&_scheduler_type) < 0)
    {
        return -1;
    }

    Py_INCREF((PyObject *)&_scheduler_type);
    if (PyModule_AddObject(module, "Scheduler",
                           (PyObject *)&_scheduler_type) != 0)
    {
        Py_DECREF((PyObject *)&_scheduler_type);
        return -1;

    }

    capi->fil_scheduler_get = fil_scheduler_get;
    capi->fil_scheduler_add_event = fil_scheduler_add_event;
    capi->fil_scheduler_switch = fil_scheduler_switch;
    capi->fil_scheduler_gl_switch = fil_scheduler_gl_switch;
    capi->fil_scheduler_greenlet = fil_scheduler_greenlet;

    return 0;
}
