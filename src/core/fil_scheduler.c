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

/* Max number of recycled FilSchedEvent nodes kept per scheduler.  Sized so
 * even high-concurrency workloads (~1000 greenlets with an event in flight
 * each) never touch malloc in steady state, while keeping the worst-case
 * cached memory small (2048 * sizeof(FilSchedEvent) ~= 112KB). */
#define FIL_SCHED_EVENT_FREELIST_MAX 2048

#define _scheduler_get() \
    (PyFilScheduler *)pthread_getspecific(_scheduler_key)
#define _scheduler_set(__x) \
    pthread_setspecific(_scheduler_key, __x)
static pthread_key_t _scheduler_key = 0;

/****************/

/* Called under sched_lock the moment an event leaves a queue, so an owner
 * holding a handle (see fil_scheduler_add_event_ref) can see that the node is
 * no longer theirs to cancel. */
static inline void _event_detached(FilSchedEvent *event)
{
    if (event->owner_ref != NULL)
    {
        *(event->owner_ref) = NULL;
        event->owner_ref = NULL;
    }
}

/**********************
 * immediate FIFO
 *********************/

static inline void _imm_push(FilSchedEventList *elist, FilSchedEvent *event)
{
    event->heap_idx = FIL_SCHED_HEAP_NOT_QUEUED;
    event->next = NULL;
    event->prev = elist->tail;
    if (elist->tail != NULL)
        elist->tail->next = event;
    else
        elist->head = event;
    elist->tail = event;
}

static inline void _imm_unlink(FilSchedEventList *elist, FilSchedEvent *event)
{
    if (event->prev != NULL)
        event->prev->next = event->next;
    else
        elist->head = event->next;
    if (event->next != NULL)
        event->next->prev = event->prev;
    else
        elist->tail = event->prev;
}

/**********************
 * timer min-heap
 *********************/

/* Below this the array is left alone; shrinking a handful of pointers is not
 * worth a realloc. */
#define FIL_SCHED_HEAP_MIN_SHRINK 128

static int _heap_reserve(FilSchedTimerHeap *heap, size_t want)
{
    FilSchedEvent **entries;
    size_t capacity;

    if (heap->capacity >= want)
        return 0;

    capacity = heap->capacity ? heap->capacity * 2 : 32;
    while (capacity < want)
        capacity *= 2;

    entries = realloc(heap->entries, capacity * sizeof(*entries));
    if (entries == NULL)
        return -1;

    heap->entries = entries;
    heap->capacity = capacity;
    return 0;
}

static void _heap_sift_up(FilSchedTimerHeap *heap, size_t idx)
{
    FilSchedEvent *event = heap->entries[idx];

    while (idx > 0)
    {
        size_t parent = (idx - 1) / 2;

        if (!FIL_EVENT_COMPARE(event, heap->entries[parent], <))
            break;
        heap->entries[idx] = heap->entries[parent];
        heap->entries[idx]->heap_idx = idx;
        idx = parent;
    }
    heap->entries[idx] = event;
    event->heap_idx = idx;
}

static void _heap_sift_down(FilSchedTimerHeap *heap, size_t idx)
{
    FilSchedEvent *event = heap->entries[idx];
    size_t half = heap->len / 2;

    while (idx < half)
    {
        size_t child = idx * 2 + 1;

        if (child + 1 < heap->len &&
            FIL_EVENT_COMPARE(heap->entries[child + 1], heap->entries[child], <))
        {
            child++;
        }
        if (!FIL_EVENT_COMPARE(heap->entries[child], event, <))
            break;
        heap->entries[idx] = heap->entries[child];
        heap->entries[idx]->heap_idx = idx;
        idx = child;
    }
    heap->entries[idx] = event;
    event->heap_idx = idx;
}

static int _heap_push(FilSchedTimerHeap *heap, FilSchedEvent *event)
{
    if (_heap_reserve(heap, heap->len + 1) < 0)
        return -1;
    heap->entries[heap->len] = event;
    event->heap_idx = heap->len;
    heap->len++;
    _heap_sift_up(heap, heap->len - 1);
    return 0;
}

static void _heap_remove_at(FilSchedTimerHeap *heap, size_t idx)
{
    FilSchedEvent *moved;

    heap->entries[idx]->heap_idx = FIL_SCHED_HEAP_NOT_QUEUED;
    heap->len--;

    if (idx != heap->len)
    {
        /* Backfill with the last entry and let it find its level.  Only one
         * of the two passes can move it, but which one depends on where the
         * hole was, so run both. */
        moved = heap->entries[heap->len];
        heap->entries[idx] = moved;
        moved->heap_idx = idx;
        _heap_sift_down(heap, idx);
        _heap_sift_up(heap, moved->heap_idx);
    }

    /* A burst of timers (a load test arming one per request, say) can leave a
     * big array behind; hand the memory back once it is mostly empty. */
    if (heap->capacity > FIL_SCHED_HEAP_MIN_SHRINK && heap->len < heap->capacity / 4)
    {
        size_t capacity = heap->capacity / 2;
        FilSchedEvent **entries = realloc(heap->entries,
                                          capacity * sizeof(*entries));

        if (entries != NULL)
        {
            heap->entries = entries;
            heap->capacity = capacity;
        }
    }
}

/**********************
 * ready-batch selection
 *********************/

static inline FilSchedEvent *_get_ready_events(PyFilScheduler *sched, struct timespec **next_run_ret)
{
    FilSchedEventList *elist = &(sched->immediate);
    FilSchedTimerHeap *heap = &(sched->timers);
    FilSchedEvent *ready_head = NULL;
    FilSchedEvent *ready_tail = NULL;
    FilSchedEvent *event;
    struct timespec now;
    int have_now = 0;

    /* Expired timers lead the batch.  They are already past a deadline they
     * asked for, whereas everything on the immediate FIFO was queued during
     * this pass and is by definition not late; both sets still run in this
     * same pass, so putting timers first costs the immediates nothing and
     * keeps sleep()/timeout wakeups from queueing behind a switch storm. */
    while (heap->len > 0)
    {
        event = heap->entries[0];
        if (!have_now)
        {
            /* The clock is read lazily: a queue that is all immediate
             * wakeups (the switch-heavy case) never reads it at all. */
            fil_timespec_now(&now);
            have_now = 1;
        }
        if (FIL_TIMESPEC_COMPARE(&(event->ts), &now, >))
            break;
        _heap_remove_at(heap, 0);
        _event_detached(event);
        event->next = NULL;
        if (ready_tail != NULL)
            ready_tail->next = event;
        else
            ready_head = event;
        ready_tail = event;
    }

    /* Then everything queued for right now, in the order it was queued, so
     * 'schedule a callback, then yield' idioms keep their relative order. */
    if ((event = elist->head) != NULL)
    {
        FilSchedEvent *cur;

        elist->head = NULL;
        elist->tail = NULL;
        for (cur = event; cur != NULL; cur = cur->next)
        {
            _event_detached(cur);
        }
        if (ready_tail != NULL)
            ready_tail->next = event;
        else
            ready_head = event;
    }

    if (ready_head == NULL)
    {
        *next_run_ret = heap->len > 0 ? &(heap->entries[0]->ts) : NULL;
        return NULL;
    }

    *next_run_ret = NULL;
    return ready_head;
}

static void _scheduler_key_delete(void *sched)
{
    if (sched != NULL)
    {
        Py_DECREF((PyObject *)sched);
    }
}

static int _scheduler_add_event(PyFilScheduler *sched, struct timespec *ts, uint32_t flags, fil_event_cb_t cb, void *cb_arg, FilSchedEvent **owner_ref)
{
    FilSchedEvent *event;
    int wake_scheduler;

    pthread_mutex_lock(&(sched->sched_lock));

    /* Recycle an event node if we can (freelist is protected by
     * sched_lock).  Falling back to malloc() under the lock is fine: it
     * only happens until the freelist warms up (or when more events are
     * in flight than FIL_SCHED_EVENT_FREELIST_MAX). */
    if ((event = sched->event_freelist) != NULL)
    {
        sched->event_freelist = event->next;
        sched->event_freelist_len--;
    }
    else
    {
        event = malloc(sizeof(*event));
        if (event == NULL)
        {
            pthread_mutex_unlock(&(sched->sched_lock));
            return -1;
        }
    }

    event->flags = flags;
    event->cb = cb;
    event->cb_arg = cb_arg;
    event->owner_ref = owner_ref;
    if (owner_ref != NULL)
    {
        *owner_ref = event;
    }

    if (ts == NULL)
    {
        /* Ready now: straight onto the FIFO, no clock, no ordering work. */
        event->ts.tv_sec = 0;
        event->ts.tv_nsec = 0;
        /* Only the empty -> non-empty transition can find the scheduler
         * asleep: if the FIFO already had an entry, the scheduler either is
         * running or is about to be woken for that one. */
        wake_scheduler = (sched->immediate.head == NULL);
        _imm_push(&(sched->immediate), event);
    }
    else
    {
        event->ts = *ts;
        if (_heap_push(&(sched->timers), event) < 0)
        {
            if (owner_ref != NULL)
            {
                *owner_ref = NULL;
            }
            /* Hand the node back rather than leaking it. */
            if (sched->event_freelist_len < FIL_SCHED_EVENT_FREELIST_MAX)
            {
                event->next = sched->event_freelist;
                sched->event_freelist = event;
                sched->event_freelist_len++;
                event = NULL;
            }
            pthread_mutex_unlock(&(sched->sched_lock));
            free(event);
            PyErr_NoMemory();
            return -1;
        }
        /* Only a new earliest deadline shortens the scheduler's sleep. */
        wake_scheduler = (event->heap_idx == 0);
    }

    /* Signal AFTER dropping sched_lock: waking the scheduler while we still
     * hold the mutex just makes it collide with the held lock and costs an
     * extra futex round trip. The predicate was published under the lock, so
     * this is safe; 'sched' remains valid for the duration of the call by the
     * caller's contract (it holds a reference directly or transitively). */
    pthread_mutex_unlock(&(sched->sched_lock));
    if (wake_scheduler)
    {
        pthread_cond_signal(&(sched->sched_cond));
    }

    return 0;
}

/*
 * Remove a still-queued event, identified by the handle its owner was given
 * by fil_scheduler_add_event_ref().
 *
 * Returns 1 if the event was ours to remove (the caller now owns whatever the
 * callback would have released -- a reference, typically), or 0 if the
 * scheduler had already taken it out of the queue to run it.  Reading the
 * handle under sched_lock is what makes that race decidable.
 */
int fil_scheduler_del_event(PyFilScheduler *sched, FilSchedEvent **owner_ref)
{
    FilSchedEvent *event;

    pthread_mutex_lock(&(sched->sched_lock));

    if ((event = *owner_ref) == NULL)
    {
        pthread_mutex_unlock(&(sched->sched_lock));
        return 0;
    }

    if (event->heap_idx == FIL_SCHED_HEAP_NOT_QUEUED)
    {
        _imm_unlink(&(sched->immediate), event);
    }
    else
    {
        _heap_remove_at(&(sched->timers), event->heap_idx);
    }

    *owner_ref = NULL;
    event->owner_ref = NULL;

    /* Removing an event never makes another one ready sooner, so there is
     * nothing to signal; the scheduler may wake once at the old deadline,
     * find nothing ready and go back to sleep. */
    if (sched->event_freelist_len < FIL_SCHED_EVENT_FREELIST_MAX)
    {
        event->next = sched->event_freelist;
        sched->event_freelist = event;
        sched->event_freelist_len++;
        event = NULL;
    }

    pthread_mutex_unlock(&(sched->sched_lock));

    free(event);

    return 1;
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
    /* Uses the vendored greenlet's no-args fast entry when available
     * (see fil_greenlet_switch_noargs in core/pyversion.h). */
    PyObject *result = fil_greenlet_switch_noargs(greenlet);
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
    self->immediate.head = self->immediate.tail = NULL;
    self->timers.entries = NULL;
    self->timers.len = 0;
    self->timers.capacity = 0;
    self->event_freelist = NULL;
    self->event_freelist_len = 0;
    self->running = 0;
    self->aborting = 0;
    /* Bind this scheduler to the creating OS thread. All greenlet switches
     * driven by this scheduler MUST happen on this thread. */
    self->thread_id = PyThread_get_thread_ident();
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

    Py_INCREF(self);
    _scheduler_set(self);

    /* Switch to the scheduler greenlet, but immediately switch back.
     * PyGreenlet_GetParent() returns a NEW reference; fil_scheduler_gl_switch()
     * takes its own reference, so release ours afterward.
     *
     * We enqueue a switch back to our parent (the greenlet that is bootstrapping
     * the scheduler) so that once the scheduler greenlet starts running its main
     * loop and processes the event queue, control returns here. */
    {
        PyGreenlet *_parent = PyGreenlet_GetParent(self->greenlet);
        int _rc = (_parent == NULL) ? -1 :
                  fil_scheduler_gl_switch(self, NULL, _parent);
        Py_XDECREF(_parent);
        if (_rc < 0)
        {
            _scheduler_set(NULL);
            Py_DECREF(self);
            Py_CLEAR(self->system_exceptions);
            Py_CLEAR(self->greenlet);
            return -1;
        }
    }
    if (_greenlet_switch(self->greenlet) < 0)
    {
        _scheduler_set(NULL);
        Py_DECREF(self);
        Py_CLEAR(self->system_exceptions);
        Py_CLEAR(self->greenlet);
        return -1;
    }

    return 0;
}

static void _sched_dealloc(PyFilScheduler *self)
{
    FilSchedEvent *event;

    while ((event = self->event_freelist) != NULL)
    {
        self->event_freelist = event->next;
        free(event);
    }
    self->event_freelist_len = 0;
    /* Any events still queued here would be a bug (each holds a reference to
     * something that would have kept this scheduler alive); the heap's array
     * is ours either way. */
    free(self->timers.entries);
    self->timers.entries = NULL;
    self->timers.len = 0;
    self->timers.capacity = 0;
    pthread_mutex_destroy(&(self->sched_lock));
    pthread_cond_destroy(&(self->sched_cond));
    Py_CLEAR(self->system_exceptions);
#if 1 /* why did I disable this? */
    _handle_greenlet_done(&(self->greenlet));
#endif
    /* The TSD slot holds a reference, so being deallocated means this
     * scheduler is no longer in *its own* thread's slot.
     *
     * It does NOT mean the running thread has no scheduler: the last
     * reference to a scheduler is often dropped by the cycle collector, which
     * runs wherever the allocation that triggered it happened -- routinely a
     * different, scheduler-owning thread. Asserting _scheduler_get() == NULL
     * aborted there, in any build with assertions enabled.
     */
    assert(_scheduler_get() != self);
    /* Respect tp_free: Python subclass instances are GC-allocated, and
     * PyObject_Del on them frees the wrong pointer (heap corruption). */
    Py_TYPE(self)->tp_free((PyObject *)self);
}

PyDoc_STRVAR(sched_fil_switch_doc, "Schedule a filament to run.");
static PyObject *_sched_fil_switch(PyFilScheduler *self, PyObject *greenlet)
{
    if (!PyGreenlet_Check(greenlet))
    {
        PyErr_SetString(PyExc_TypeError, "fil_switch() expects a filament/greenlet.");
        return NULL;
    }

    /*
     * Cross-thread guard for the one untrusted entry point.
     *
     * fil_switch() accepts an arbitrary greenlet from Python and enqueues it
     * onto THIS scheduler, which will later PyGreenlet_Switch() to it on the
     * scheduler's own thread. If the greenlet is owned by a different OS thread
     * that is the classic "switch to a greenlet owned by another thread" crash.
     *
     * greenlet 3.x exposes no clean public C API to read a greenlet's owning
     * thread, so we apply the strongest guard we can express portably: require
     * that fil_switch() is called from the scheduler's own thread. Legitimate
     * use (a filament running under this scheduler handing control to a peer
     * greenlet on the same thread) always satisfies this; a stray call from
     * another thread -- the case that leads to the illegal cross-thread switch
     * -- is rejected with a clear error instead of crashing. (Internal wakeups
     * from the I/O thread / thread pool do NOT come through here; they call
     * fil_scheduler_gl_switch() directly with a greenlet already known to
     * belong to this scheduler's thread.)
     */
    if (self->thread_id != PyThread_get_thread_ident())
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "fil_switch() must be called from the scheduler's own "
                        "thread (cross-thread greenlet switch is illegal)");
        return NULL;
    }

    /* fil_scheduler_gl_switch() reports enqueue failures; propagate any error
     * rather than silently swallowing it. */
    if (fil_scheduler_gl_switch(self, NULL, (PyGreenlet *)greenlet) < 0)
    {
        return NULL;
    }
    Py_RETURN_NONE;
}

/*
 * FIXME
 *
 * _fil_filament_main() will propagate exceptions back to
 * the scheduler. this is so that things like ^C, etc can
 * be raised back to the scheduler's parent greenthread,
 * which will likely cause an exit.
 *
 * If it's not a 'system exception', we just forget about
 * it here, because the _fil_filament_main() will also
 * send the exception back to whoever might be waiting
 * for the thread to finish.
 *
 * But, I think we will want to dump a traceback to stderr
 * when no one is waiting on the thread, but I'm not sure
 * we can really detect that. Maybe we only do it if we
 * have a filament.spawn() that doesn't return a Filament.
 *
 * In any case, we're also called here from paths where
 * an exception may have been raised outside of a Filament,
 * but we can't really tell. Perhaps some of this logic
 * needs to be moved or copied to _greenlet_switch() after
 * things are switched back.
 */
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
        /* PyGreenlet_GetParent() returns a NEW reference; PyGreenlet_Throw()
         * borrows its target, so release our parent ref afterward. */
        {
            PyGreenlet *_parent = PyGreenlet_GetParent(self->greenlet);
            PyGreenlet_Throw(_parent, exc_type, val, tb);
#if 0
            /* Throw() automatically switches */
            _greenlet_switch(_parent);
#endif
            Py_XDECREF(_parent);
        }
        Py_DECREF(exc_type);
        Py_XDECREF(val);
        Py_XDECREF(tb);
        return;
    }

    /* Squash other exceptions */
#if 0
    {
    PyObject *res;
    fprintf(stderr, "Squashing exception in greenlet:\n");
    fprintf(stderr, "--------------------------------\n");
    res = fil_format_exception(exc_type, val, tb);
    if (res == NULL)
    {
        /* shouldn't happen -- blah */
        PyErr_Clear();
        PyObject_Print(exc_type, stderr, 0);
        printf("\n");
        PyObject_Print(val, stderr, 0);
        printf("\n");
        PyObject_Print(tb, stderr, 0);
        printf("\n");
        fprintf(stderr, "--------------------------------\n");
    }
    else
    {
        PyObject_Print(res, stderr, 0);
        printf("\n");
        fprintf(stderr, "--------------------------------\n");
        Py_DECREF(res);
    }
    }
#endif
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
    FilSchedEvent *done_events;
    int err;

    /* Allow other threads to run. */
    self->thread_state = PyEval_SaveThread();

    pthread_mutex_lock(&(self->sched_lock));
    self->running = 1;
    while (!self->aborting || self->immediate.head != NULL || self->timers.len)
    {
        ready_events = _get_ready_events(self, &wait_time);
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

        /* NOTE: we deliberately keep the per-event RestoreThread/SaveThread
         * pair (rather than holding the GIL across the whole batch): the
         * voluntary GIL release after every event is an important scheduling
         * point for real OS threads (thread-pool workers, io users) that are
         * competing for the GIL -- batching it measured ~3x slower on the
         * logging-from-threadpool (#137) workload. */
        done_events = NULL;
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
            /* Stash processed nodes locally; they are returned to the
             * (sched_lock protected) freelist in one batch below. */
            event->next = done_events;
            done_events = event;
        }

        pthread_mutex_lock(&(self->sched_lock));

        while ((event = done_events) != NULL)
        {
            done_events = event->next;
            if (self->event_freelist_len < FIL_SCHED_EVENT_FREELIST_MAX)
            {
                event->next = self->event_freelist;
                self->event_freelist = event;
                self->event_freelist_len++;
            }
            else
            {
                free(event);
            }
        }
    }

    self->running = 0;
    pthread_cond_signal(&(self->sched_cond));
    pthread_mutex_unlock(&(self->sched_lock));

    /* Block threads */
    PyEval_RestoreThread(self->thread_state);
    self->thread_state = NULL;

    /* Clear the slot BEFORE releasing the reference it held.  If this is the
     * last reference, _sched_dealloc() runs inside the Py_DECREF, and it must
     * not find this thread still pointing at the scheduler being freed.  Note
     * that after the Py_DECREF 'self' may already be gone, so nothing may
     * read its fields (reading Py_REFCNT(self) here was a use-after-free). */
    _scheduler_set(NULL);
    Py_DECREF(self);

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
         * XXX: true if greenlet.getcurrent() == self->greenlet
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

PyDoc_STRVAR(sched_queue_depth_doc,
"queue_depth() -> (immediate_count, timer_count)\n\
\n\
How many events are queued right now: ready-to-run ones, and ones waiting\n\
for a deadline.  Diagnostic -- it is what tells you whether cancelled\n\
timeouts are actually leaving the queue.");
static PyObject *_sched_queue_depth(PyFilScheduler *self, PyObject *args)
{
    FilSchedEvent *event;
    Py_ssize_t immediate = 0;
    Py_ssize_t timers;

    pthread_mutex_lock(&(self->sched_lock));
    for (event = self->immediate.head; event != NULL; event = event->next)
    {
        immediate++;
    }
    timers = (Py_ssize_t)self->timers.len;
    pthread_mutex_unlock(&(self->sched_lock));

    return Py_BuildValue("(nn)", immediate, timers);
}

static PyMethodDef _sched_methods[] = {
    {"queue_depth", (PyCFunction)_sched_queue_depth, METH_NOARGS, sched_queue_depth_doc},
    {"fil_switch", (PyCFunction)_sched_fil_switch, METH_O, sched_fil_switch_doc},
    {"main", (PyCFunction)_sched_main, METH_VARARGS, sched_main_doc},
    {"abort", (PyCFunction)_sched_abort, METH_VARARGS, sched_abort_doc},
    {"switch", (PyCFunction)_sched_switch, METH_NOARGS, sched_switch_doc},
    {"greenlet", (PyCFunction)_sched_greenlet, METH_NOARGS, sched_greenlet_doc},
    { NULL, NULL }
};

static PyTypeObject _scheduler_type = {
    PyVarObject_HEAD_INIT(0, 0)                 /* Must fill in type
                                                   value later */
    "_filament.Scheduler",                      /* tp_name */
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
    FIL_DEFAULT_TPFLAGS,                        /* tp_flags */
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

PyFilScheduler *fil_scheduler_get(int create)
{
    PyFilScheduler *self = _scheduler_get();

    if ((self != NULL) || !create)
    {
        Py_XINCREF(self);
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
   return _scheduler_add_event(sched, ts, flags, cb, cb_arg, NULL);
}

/*
 * Same, but hand the caller a handle on the queued event: *owner_ref is set to
 * the node while it is queued and NULLed (under sched_lock) as soon as the
 * event leaves the queue.  Pass that same address to fil_scheduler_del_event()
 * to cancel.  The handle must outlive the event -- it lives in the owning
 * object, which the event holds a reference to.
 */
int fil_scheduler_add_event_ref(PyFilScheduler *sched, struct timespec *ts,
                       uint32_t flags, fil_event_cb_t cb, void *cb_arg,
                       FilSchedEvent **owner_ref)
{
   return _scheduler_add_event(sched, ts, flags, cb, cb_arg, owner_ref);
}

int fil_scheduler_switch(PyFilScheduler *sched)
{
    return _greenlet_switch(sched->greenlet);
}

/*
 * Queue a deferred switch to 'greenlet' via the scheduler's event queue.
 *
 * This is the heart of the deferred-switch design: rather than switching to a
 * greenlet directly (which forces an immediate context switch and makes
 * cross-thread bugs easy), we enqueue an event that the scheduler greenlet
 * will run (via _greenlet_event_switch) on ITS OWN thread. That keeps every
 * PyGreenlet_Switch on the thread that owns both the scheduler and the target
 * greenlet.
 *
 * IMPORTANT: this enqueue is intentionally safe to call from *any* OS thread.
 * The event list is protected by sched_lock, and this is exactly how off-CPU
 * helpers (the I/O thread and the thread pool workers) wake up a filament that
 * is parked on its scheduler: they call in from their own thread to enqueue a
 * switch that the scheduler greenlet then performs on the scheduler's thread.
 * Therefore we must NOT reject based on the *calling* thread here -- doing so
 * breaks every cross-thread wakeup. The invariant that actually matters is
 * that the target 'greenlet' belongs to the scheduler's thread; that is
 * guaranteed by construction for the internal callers (they always enqueue a
 * greenlet obtained on the scheduler's own thread), and it is enforced for the
 * one untrusted entry point in _sched_fil_switch().
 *
 * Refcount contract: we take a reference to 'greenlet' here; ownership is
 * transferred to the event, and _greenlet_event_switch() drops it after the
 * switch returns. If we cannot enqueue the event (malloc failure) we MUST
 * drop that reference and report failure -- otherwise we would both leak the
 * reference AND silently drop the wakeup, hanging the greenlet forever.
 *
 * Returns 0 on success, -1 on failure (with a Python exception set).
 */
int fil_scheduler_gl_switch(PyFilScheduler *sched, struct timespec *ts, PyGreenlet *greenlet)
{
    Py_INCREF(greenlet);
    if (fil_scheduler_add_event(sched, ts, 0,
                                (fil_event_cb_t)_greenlet_event_switch,
                                greenlet) < 0)
    {
        /* Enqueue failed (out of memory). Undo our incref and surface the
         * error so the caller doesn't wait on a wakeup that will never come. */
        Py_DECREF(greenlet);
        if (!PyErr_Occurred())
        {
            PyErr_SetNone(PyExc_MemoryError);
        }
        return -1;
    }

    return 0;
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
    capi->fil_scheduler_add_event_ref = fil_scheduler_add_event_ref;
    capi->fil_scheduler_del_event = fil_scheduler_del_event;
    capi->fil_scheduler_switch = fil_scheduler_switch;
    capi->fil_scheduler_gl_switch = fil_scheduler_gl_switch;
    capi->fil_scheduler_greenlet = fil_scheduler_greenlet;

    return 0;
}
