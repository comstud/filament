#ifndef __FIL_WAITER_H__
#define __FIL_WAITER_H__

#include "core/filament.h"
#include <stddef.h>

typedef struct _fil_waiter FilWaiter;
typedef struct _fil_waiterlist FilWaiterList;

struct _fil_waiterlist {
    FilWaiterList *prev;
    FilWaiterList *next;
};

struct _fil_waiter {
    PyFilScheduler *sched;
    PyGreenlet *gl;
#define fil_waiter_set_signaled(waiter) (waiter)->flags |= FIL_WAITER_FLAGS_SIGNALED
#define fil_waiter_signaled(waiter) ((waiter)->flags & FIL_WAITER_FLAGS_SIGNALED)
#define fil_waiter_set_waiting(waiter) (waiter)->flags |= FIL_WAITER_FLAGS_WAITING
#define fil_waiter_waiting(waiter)  ((waiter)->flags & FIL_WAITER_FLAGS_WAITING)
#define fil_waiter_set_timed(waiter) (waiter)->flags |= FIL_WAITER_FLAGS_TIMED
#define fil_waiter_timed(waiter)  ((waiter)->flags & FIL_WAITER_FLAGS_TIMED)
    #define FIL_WAITER_FLAGS_SIGNALED   0x001
    #define FIL_WAITER_FLAGS_WAITING    0x002
    #define FIL_WAITER_FLAGS_TIMED      0x004
    unsigned int flags;
    /* 'refcnt' is a plain (non-atomic) counter. This is safe because of a
     * strict invariant: a waiter is only ever incref'd/decref'd while holding
     * the GIL, and every one of those operations happens on the thread that
     * owns the waiter's scheduler (the greenlet-driven producer/consumer both
     * run under that single scheduler thread). Signalers (which may run on
     * another thread, possibly WITHOUT the GIL -- e.g. the io thread) never
     * touch refcnt; the waiter side re-acquires waiter_lock after waking and
     * before dropping its reference, which orders any concurrent signaler's
     * last touch of the waiter strictly before the free. If a code path ever
     * needs to change refcnt from another thread, this must become atomic. */
    unsigned int refcnt;
    /* Handle on the scheduler event that would fire this waiter's timeout,
     * while it is queued (see fil_scheduler_add_event_ref).  The scheduler
     * NULLs it when the event leaves the queue, so a wait that is signaled
     * before its deadline can take the event back out instead of leaving it
     * to occupy the timer heap -- and hold a reference to this waiter -- for
     * the rest of the timeout. */
    FilSchedEvent *timeout_event;
    pthread_mutex_t waiter_lock;
    pthread_cond_t waiter_cond;
    FilWaiterList waiter_list;
};

/*
 * Freelist of FilWaiter structures.
 *
 * Every blocking operation (lock/semaphore/queue/message wait, blocking io,
 * thread-pool round trip) allocates one FilWaiter and frees it again when the
 * wait completes, paying for a malloc/free pair AND a pthread mutex+cond
 * init/destroy cycle each time.  Recycle them instead: pooled waiters keep
 * their mutex/cond initialized, so a warm wait skips all four.
 *
 * Locking: NONE, deliberately.  fil_waiter_alloc() and fil_waiter_decref()
 * are only ever called with the GIL held (see the 'refcnt' comment above:
 * refcnt is likewise GIL-protected, and alloc/decref happen on the Python
 * side of every path; off-GIL signalers never alloc or decref).  The GIL
 * therefore serializes all freelist access.  NOTE: these statics are
 * per-translation-unit (this is a header), which is fine -- each pool is
 * just a cache of interchangeable malloc'd blocks; a block allocated via one
 * TU's pool may be released into another's without harm.
 */
#ifndef FIL_WAITER_FREELIST_MAX
#define FIL_WAITER_FREELIST_MAX 1024
#endif
static FilWaiter *_fil_waiter_freelist = NULL;
static int _fil_waiter_freelist_len = 0;

static inline FilWaiter *fil_waiter_alloc(void)
{
    FilWaiter *waiter;

    if ((waiter = _fil_waiter_freelist) != NULL) {
        _fil_waiter_freelist = (FilWaiter *)(void *)waiter->waiter_list.next;
        _fil_waiter_freelist_len--;
        /* mutex/cond are still initialized from the previous life */
        waiter->sched = NULL;
        waiter->gl = NULL;
        waiter->flags = 0;
        waiter->refcnt = 1;
        waiter->timeout_event = NULL;
        return waiter;
    }

    waiter = malloc(sizeof(FilWaiter));
    if (waiter == NULL) {
        PyErr_SetString(PyExc_MemoryError, "failed to alloc FilWaiter");
    } else {
        waiter->sched = NULL;
        waiter->gl = NULL;
        waiter->flags = 0;
        waiter->refcnt = 1;
        waiter->timeout_event = NULL;
        pthread_mutex_init(&(waiter->waiter_lock), NULL);
        pthread_cond_init(&(waiter->waiter_cond), NULL);
    }

    return waiter;
}

static inline void fil_waiter_decref(FilWaiter *waiter)
{
    if (--waiter->refcnt == 0) {
        Py_CLEAR(waiter->sched);
        Py_CLEAR(waiter->gl);
        if (_fil_waiter_freelist_len < FIL_WAITER_FREELIST_MAX) {
            waiter->waiter_list.next = (FilWaiterList *)(void *)_fil_waiter_freelist;
            _fil_waiter_freelist = waiter;
            _fil_waiter_freelist_len++;
        } else {
            pthread_mutex_destroy(&(waiter->waiter_lock));
            pthread_cond_destroy(&(waiter->waiter_cond));
            free(waiter);
        }
    }
}


static inline void _fil_waiter_handle_timeout(PyFilScheduler *sched, FilWaiter *waiter)
{
    PyGreenlet *gl;

    /* Runs as a scheduler event callback, i.e. on the scheduler's own thread,
     * with the GIL held. Serialize against off-thread signalers: if the waiter
     * has already been signaled, its wakeup switch is (or will be) enqueued by
     * the signaler, so enqueuing a second one here would switch to the
     * greenlet again after it completed. */
    pthread_mutex_lock(&(waiter->waiter_lock));
    gl = fil_waiter_signaled(waiter) ? NULL : waiter->gl;
    if (gl != NULL)
    {
        /* On the rare enqueue (OOM) failure gl_switch drops its own reference
         * and sets a Python error, which the scheduler loop surfaces after
         * the callback returns; there is no ref leak. */
        (void)fil_scheduler_gl_switch(sched, NULL, gl);
    }
    pthread_mutex_unlock(&(waiter->waiter_lock));
    fil_waiter_decref(waiter);
}

/*
 * Scheduler event callback used for GIL-free wakeups: switch to the parked
 * greenlet using a BORROWED reference. This is safe because the reference is
 * backed by waiter->gl, which the parked greenlet only releases after it
 * resumes -- i.e. after this switch has happened -- and while it is running it
 * is kept alive by the greenlet runtime itself.
 */
static inline void _fil_waiter_switch_event_cb(PyFilScheduler *sched, void *cb_arg)
{
    PyObject *result = fil_greenlet_switch_noargs((PyGreenlet *)cb_arg);
    Py_XDECREF(result);
}

static inline int fil_waiter_wait(FilWaiter *waiter, struct timespec *ts, PyObject *timeout_exc)
{
    int err;

    if (fil_waiter_signaled(waiter))
    {
        return 0;
    }

    Py_XSETREF(waiter->sched, fil_scheduler_get(0));
    if (waiter->sched == NULL)
    {
        PyThreadState *thr_state;

        for(;;)
        {
            int signaled;

            thr_state = PyEval_SaveThread();
            pthread_mutex_lock(&(waiter->waiter_lock));

            /* race with GIL unlocked?  (signalers may run without the GIL,
             * so the SIGNALED/WAITING handshake happens under waiter_lock) */
            if (fil_waiter_signaled(waiter))
            {
                pthread_mutex_unlock(&(waiter->waiter_lock));
                PyEval_RestoreThread(thr_state);
                break;
            }

            fil_waiter_set_waiting(waiter);

            err = fil_pthread_cond_wait_min(&(waiter->waiter_cond),
                                            &(waiter->waiter_lock), ts);

            pthread_mutex_unlock(&(waiter->waiter_lock));
            PyEval_RestoreThread(thr_state);

            /* Barrier + claim. Re-read SIGNALED under waiter_lock (the old
             * unlocked read here raced a signaler that was still inside its
             * waiter_lock critical section, allowing our caller to free the
             * waiter under it). A signaler holds waiter_lock from setting
             * SIGNALED through its last touch of the waiter, so acquiring
             * the lock orders it strictly before any free/recycle by our
             * caller. If we are about to leave WITHOUT having been signaled
             * (timeout), claim the waiter by setting SIGNALED ourselves so
             * any later signaler returns immediately instead of touching a
             * waiter that is being torn down; our caller's list-removal +
             * decref run in the same GIL window, and GIL-holding signalers
             * cannot start a new critical section before that. */
            pthread_mutex_lock(&(waiter->waiter_lock));
            signaled = fil_waiter_signaled(waiter);
            if (!signaled && err == ETIMEDOUT)
            {
                fil_waiter_set_signaled(waiter);
            }
            pthread_mutex_unlock(&(waiter->waiter_lock));

            if (signaled)
            {
                break;
            }

            if (err == ETIMEDOUT)
            {
                fil_set_timeout_exc(timeout_exc);
                return -err;
            }

            /* check signals here so we don't lock up forever */
            if (PyErr_CheckSignals())
            {
                /* Exception exit: claim as above (CheckSignals may have run
                 * Python code that released the GIL, so re-check under the
                 * lock; a concurrent signal just means the claim is a
                 * no-op and list removal by the caller is idempotent). */
                pthread_mutex_lock(&(waiter->waiter_lock));
                fil_waiter_set_signaled(waiter);
                pthread_mutex_unlock(&(waiter->waiter_lock));
                return -1;
            }
        }

        return 0;
    }

    waiter->gl = PyGreenlet_GetCurrent();

    /* Publish the parked state under waiter_lock. A signaler (possibly on
     * another thread, possibly WITHOUT the GIL -- e.g. the io thread) either
     * observes WAITING+gl and enqueues our wakeup, or wins the race by
     * setting SIGNALED first, which we observe here before parking. */
    pthread_mutex_lock(&(waiter->waiter_lock));
    if (fil_waiter_signaled(waiter))
    {
        pthread_mutex_unlock(&(waiter->waiter_lock));
        Py_CLEAR(waiter->gl);
        return 0;
    }
    fil_waiter_set_waiting(waiter);
    if (ts != NULL)
    {
        fil_waiter_set_timed(waiter);
    }
    pthread_mutex_unlock(&(waiter->waiter_lock));

    if (ts != NULL)
    {
        waiter->refcnt++;
        fil_scheduler_add_event_ref(waiter->sched, ts, 0,
                                    (fil_event_cb_t)_fil_waiter_handle_timeout,
                                    waiter, &(waiter->timeout_event));
    }

    fil_scheduler_switch(waiter->sched);

    /* Signaled (or thrown into) before the deadline?  Take the timeout event
     * back out of the scheduler.  Leaving it queued would hold this waiter --
     * and its slot in the timer heap -- for the rest of the timeout, which for
     * the 60s timeouts network clients like to use means a wait that finished
     * in a millisecond keeps its memory for a minute.  del_event tells us
     * whether we won the race against the scheduler running it; if we did, the
     * reference the event held is ours to drop. */
    if (waiter->timeout_event != NULL &&
        fil_scheduler_del_event(waiter->sched, &(waiter->timeout_event)))
    {
        fil_waiter_decref(waiter);
    }

    /* Synchronization barrier: a signaler's last touch of this waiter happens
     * under waiter_lock (see fil_waiter_signal), and we can only have resumed
     * after it enqueued our switch from inside that critical section. Taking
     * the lock here therefore guarantees the signaler is completely done with
     * the waiter before we release our references / potentially free it. */
    pthread_mutex_lock(&(waiter->waiter_lock));
    err = fil_waiter_signaled(waiter) ? 1 : 0;
    pthread_mutex_unlock(&(waiter->waiter_lock));

    Py_CLEAR(waiter->gl);

    if (!err)
    {
        if (PyErr_Occurred())
        {
            return -1;
        }

        /* must be a timeout */
        /* FIXME: hm, no. i believe we can get here if we receive
         * a signal in the scheduler while in its cond_wait loop.
         * if the signal causes a system exception, the scheduer
         * will raise it in the scheduler's parent greenthread,
         * but that may not be in this one. the exception is
         * otherwise is nuked, so we wouldn't see it here.
         *
         * I believe this is what caused me to see this exception
         * when I ^C'd a socket server while blocked in a recv()
         */
        fil_set_timeout_exc(timeout_exc);

        return -ETIMEDOUT;
    }

    return 0;
}

/*
 * Wake the waiter (common implementation).
 *
 * 'have_gil' says whether the CALLER holds the GIL:
 *   - fil_waiter_signal()       -- caller holds the GIL (all Python-driven
 *     signalers: the locking / queue / message primitives, thread-pool
 *     workers, socket teardown).
 *   - fil_waiter_signal_nogil() -- caller does NOT hold the GIL (the io
 *     thread's event callbacks). Such callers may only ever signal UNTIMED
 *     waiters (io waits never pass a ts), which keeps this path free of any
 *     Python API calls.
 *
 * Wakeup mechanics:
 *   - For UNTIMED greenlet waiters the wakeup switch is enqueued with a
 *     BORROWED greenlet reference; see _fil_waiter_switch_event_cb for why
 *     that is safe: an untimed parked greenlet can only be resumed by the
 *     event we enqueue here, so waiter->gl keeps it alive until then.
 *   - For TIMED waiters the concurrent timeout event can resume (and
 *     potentially finish) the greenlet before our enqueued switch runs, so
 *     we take a real reference via fil_scheduler_gl_switch (GIL required --
 *     guaranteed because timed waiters are only ever signaled by GIL-holding
 *     callers).
 *   - For OS-thread waiters (sched == NULL) a GIL-holding caller drops the
 *     GIL around the condvar signal: the woken thread almost always needs
 *     the GIL next, and this voluntary release is a scheduling point that
 *     matters a lot for lock-heavy multi-thread workloads (e.g. logging from
 *     thread-pool workers under a monkey-patched hub).
 */
static inline void _fil_waiter_signal_common(FilWaiter *waiter, int have_gil)
{
    PyFilScheduler *sched;
    PyGreenlet *gl;
    PyThreadState *thr_state;

    pthread_mutex_lock(&(waiter->waiter_lock));

    if (fil_waiter_signaled(waiter))
    {
        pthread_mutex_unlock(&(waiter->waiter_lock));
        return;
    }

    fil_waiter_set_signaled(waiter);

    if (!fil_waiter_waiting(waiter))
    {
        pthread_mutex_unlock(&(waiter->waiter_lock));
        return;
    }

    sched = waiter->sched;

    if (sched == NULL)
    {
        if (have_gil)
        {
            /* We don't necessarily need to release the GIL but this
             * might be better to wake up other threads sooner. */
            thr_state = PyEval_SaveThread();
            pthread_cond_signal(&(waiter->waiter_cond));
            pthread_mutex_unlock(&(waiter->waiter_lock));
            PyEval_RestoreThread(thr_state);
        }
        else
        {
            pthread_cond_signal(&(waiter->waiter_cond));
            pthread_mutex_unlock(&(waiter->waiter_lock));
        }
        return;
    }

    gl = waiter->gl;
    if (gl != NULL)
    {
        /* Wake the waiting greenlet by enqueuing a switch onto its home
         * scheduler; the scheduler greenlet performs the actual switch on its
         * own thread (never here). Enqueue while still holding waiter_lock so
         * the resumed greenlet's barrier (see fil_waiter_wait) cannot
         * complete -- and free the waiter -- until we are done with it. */
        if (fil_waiter_timed(waiter))
        {
            /* GIL held (timed waiters are only signaled by GIL-holding
             * callers); takes its own greenlet reference. */
            (void)fil_scheduler_gl_switch(sched, NULL, gl);
        }
        else
        {
            /* Works with or without the GIL; borrowed reference (see the
             * contract above). On the catastrophic OOM enqueue failure there
             * is nothing safe we can do without the GIL; the wakeup is lost
             * (as it would be lost by the process dying of OOM moments later
             * anyway). */
            (void)fil_scheduler_add_event(sched, NULL, 0,
                                          _fil_waiter_switch_event_cb, gl);
        }
    }

    pthread_mutex_unlock(&(waiter->waiter_lock));

    return;
}

/* Signal from a GIL-holding caller. */
static inline void fil_waiter_signal(FilWaiter *waiter)
{
    _fil_waiter_signal_common(waiter, 1);
}

/* Signal from a caller that does NOT hold the GIL (io thread). The waiter
 * MUST be untimed. */
static inline void fil_waiter_signal_nogil(FilWaiter *waiter)
{
    _fil_waiter_signal_common(waiter, 0);
}

#define _fil_waiterlist_empty(waiter_list) ((waiter_list)->next == (waiter_list))

#define fil_waiterlist_init(head) \
    _fil_waiterlist_init(&(head))

#define fil_waiterlist_entry(cur) \
    (FilWaiter *)((char *)cur - offsetof(FilWaiter, waiter_list))

#define fil_waiterlist_empty(waiter_list) ((waiter_list).next == &(waiter_list))

#define fil_waiterlist_wait(waiter_list, ts, exc) _fil_waiterlist_wait(&(waiter_list), ts, exc)
#define fil_waiterlist_signal_first(waiter_list) _fil_waiterlist_signal_first(&(waiter_list))
#define fil_waiterlist_signal_all(waiter_list) _fil_waiterlist_signal_all(&(waiter_list))

static inline void _fil_waiterlist_init(FilWaiterList *list)
{
    list->next = list;
    list->prev = list;
}

static inline void _fil_waiterlist_add(FilWaiterList *head, FilWaiter *waiter)
{
    FilWaiterList *prev = head->prev;
    FilWaiterList *entry = &(waiter->waiter_list);

    entry->prev = prev;
    entry->next = head;
    head->prev = entry;
    prev->next = entry;
}

static inline void _fil_waiterlist_del(FilWaiterList *entry)
{
    FilWaiterList *next = entry->next;
    FilWaiterList *prev = entry->prev;

    next->prev = prev;
    prev->next = next;
}

static inline int _fil_waiterlist_wait(FilWaiterList *waiter_list, struct timespec *ts, PyObject *timeout_exc)
{
    int err;
    FilWaiter *waiter = fil_waiter_alloc();

    if (waiter == NULL) {
        return -1;
    }

    _fil_waiterlist_add(waiter_list, waiter);

    err = fil_waiter_wait(waiter, ts, timeout_exc);
    if (err)
    {
        _fil_waiterlist_del(&(waiter->waiter_list));
    }

    fil_waiter_decref(waiter);

    return err;
}

static inline void __fil_waiterlist_signal_first(FilWaiterList *waiter_list)
{
    FilWaiterList *wl = waiter_list->next;
    _fil_waiterlist_del(wl);
    /* Self-point the detached entry so a second _fil_waiterlist_del (the
     * waiting side's error path, racing this signal) degenerates into
     * harmless self-assignment instead of corrupting the list through the
     * entry's stale neighbor pointers. */
    wl->next = wl;
    wl->prev = wl;
    fil_waiter_signal(fil_waiterlist_entry(wl));
}

static inline void _fil_waiterlist_signal_all(FilWaiterList *waiter_list)
{
    while (!_fil_waiterlist_empty(waiter_list))
    {
        __fil_waiterlist_signal_first(waiter_list);
    }
}

static inline void _fil_waiterlist_signal_first(FilWaiterList *waiter_list)
{
    if (!_fil_waiterlist_empty(waiter_list))
    {
        __fil_waiterlist_signal_first(waiter_list);
    }
}

#endif /* __FIL_WAITER_H__ */
