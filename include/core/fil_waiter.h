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
    /* A wakeup switch event has been queued for this waiter and has not run
     * yet.  Set and cleared under waiter_lock; see the wakeup contract below. */
    #define FIL_WAITER_FLAGS_SWITCH_PENDING 0x008
/* fil_waiter_wait() return code: signaled, but an exception is pending too.
 * See the contract above fil_waiter_wait(). */
#define FIL_WAITER_SIGNALED_UNWIND 1
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
    /* Handle on the queued wakeup switch, so a greenthread that resumed by
     * some other route (a kill(), an expiring Timeout) can take it back out
     * instead of leaving it to switch into a greenlet that has moved on. */
    FilSchedEvent *switch_event;
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
        waiter->switch_event = NULL;
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
        waiter->switch_event = NULL;
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


/*
 * THE WAKEUP CONTRACT.
 *
 * Waking a parked greenthread means queueing a scheduler event that switches
 * into it.  Whoever queues that event may be running WITHOUT the GIL (the io
 * thread signals that way), so it cannot touch a Python refcount.  The
 * tempting shortcut -- have the event carry a *borrowed* pointer to
 * waiter->gl -- is unsound, and do not reach for it again: it assumes a
 * parked greenlet only ever resumes via that very event, and kill() breaks
 * the assumption.  Throwing into a parked greenlet resumes it by a completely
 * different route, leaving the queued switch pointing at a greenlet that then
 * finishes and is deallocated (a use-after-free in the scheduler), or, if the
 * greenlet survives to park somewhere else, resuming it in the middle of an
 * unrelated wait.
 *
 * So the event carries the WAITER instead, and:
 *
 *   - fil_waiter_wait() reserves one waiter reference before parking, while it
 *     still holds the GIL.  Whoever queues the wakeup consumes that
 *     reservation, so no off-GIL refcounting is needed and the waiter cannot
 *     be freed under the event.
 *   - the queueing side clears WAITING under waiter_lock, so at most one
 *     wakeup is ever outstanding and a later signaler will not queue a second.
 *   - a greenthread that resumes by any other route clears waiter->gl and
 *     cancels the queued event (fil_scheduler_del_event), both under
 *     waiter_lock.  If it loses the race and the event is already running, the
 *     callback finds gl NULL and does nothing.
 */

/* Queue the wakeup switch for a parked waiter.  Caller holds waiter_lock and
 * has checked that the waiter is still WAITING; the GIL may or may not be
 * held.  Consumes the wakeup reservation on success. */
static inline void _fil_waiter_queue_switch(FilWaiter *waiter, PyFilScheduler *sched);

static inline void _fil_waiter_handle_timeout(PyFilScheduler *sched, FilWaiter *waiter)
{
    /* Runs as a scheduler event callback, i.e. on the scheduler's own thread,
     * with the GIL held. Serialize against off-thread signalers: if the waiter
     * has already been signaled, its wakeup switch is (or will be) enqueued by
     * the signaler, so enqueuing a second one here would switch to the
     * greenlet again after it completed. */
    pthread_mutex_lock(&(waiter->waiter_lock));
    if (!fil_waiter_signaled(waiter) && fil_waiter_waiting(waiter))
    {
        _fil_waiter_queue_switch(waiter, sched);
    }
    pthread_mutex_unlock(&(waiter->waiter_lock));
    /* Our own reference, taken when this timeout was armed. */
    fil_waiter_decref(waiter);
}

/*
 * Scheduler event callback for a queued wakeup: switch into the parked
 * greenlet.  Runs on the scheduler's thread with the GIL held, so it can take
 * a real reference to the greenlet for the duration of the switch.
 *
 * waiter->gl is NULL if the greenthread already resumed some other way (see
 * the wakeup contract), in which case there is nothing to wake and the switch
 * MUST NOT happen -- that is the difference between this and the old borrowed
 * pointer.
 */
static inline void _fil_waiter_switch_event_cb(PyFilScheduler *sched, void *cb_arg)
{
    FilWaiter *waiter = (FilWaiter *)cb_arg;
    PyGreenlet *gl;

    pthread_mutex_lock(&(waiter->waiter_lock));
    gl = waiter->gl;
    Py_XINCREF(gl);
    pthread_mutex_unlock(&(waiter->waiter_lock));

    if (gl != NULL)
    {
        /* The woken greenthread runs INSIDE this switch, and while it is
         * running it settles who owns the reservation by looking at
         * SWITCH_PENDING -- so the flag must still say "an event is running
         * for you" here.  Clearing it before the switch would let the
         * greenthread conclude nobody had queued a wakeup and drop the
         * reservation that this callback is about to drop as well. */
        PyObject *result = fil_greenlet_switch_noargs(gl);
        Py_XDECREF(result);
        Py_DECREF(gl);
    }

    pthread_mutex_lock(&(waiter->waiter_lock));
    waiter->flags &= ~FIL_WAITER_FLAGS_SWITCH_PENDING;
    pthread_mutex_unlock(&(waiter->waiter_lock));

    /* The reservation fil_waiter_wait() made for us. */
    fil_waiter_decref(waiter);
}

static inline void _fil_waiter_queue_switch(FilWaiter *waiter, PyFilScheduler *sched)
{
    /* Only one wakeup may be outstanding: dropping WAITING here stops a later
     * signaler (or the timeout callback) from queueing a second switch. */
    waiter->flags &= ~FIL_WAITER_FLAGS_WAITING;

    if (fil_scheduler_add_event_ref(sched, NULL, 0,
                                    _fil_waiter_switch_event_cb, waiter,
                                    &(waiter->switch_event)) < 0)
    {
        /* Enqueue failed (OOM). There is nothing safe to do here without the
         * GIL, so the wakeup is lost -- as it would be when the process dies
         * of OOM moments later anyway. The reservation stays with the waiter,
         * which is correct: no event owns it. */
        waiter->switch_event = NULL;
        return;
    }
    waiter->flags |= FIL_WAITER_FLAGS_SWITCH_PENDING;
}

/*
 * Wait for this waiter to be signaled.
 *
 * Returns:
 *   0                          signaled: whatever the signal handed over
 *                              (lock ownership, a semaphore count, a queued
 *                              item, a completed job) is yours.
 *   FIL_WAITER_SIGNALED_UNWIND signaled AND an exception is pending: the
 *                              hand-over happened, but this greenthread was
 *                              thrown into (kill(), an expiring Timeout) in
 *                              the same wakeup and is on its way out.  The
 *                              caller MUST pass the hand-over on -- to the
 *                              next waiter, or back to the primitive -- and
 *                              then return failure, letting the exception
 *                              propagate.  Ignore it and a killed greenthread
 *                              walks off with a lock nobody can release, and
 *                              acquire() hands back a result with an
 *                              exception set.
 *   -ETIMEDOUT                 the deadline passed (timeout_exc is pending).
 *   -1                         not signaled; an exception is pending.
 */
static inline int fil_waiter_wait(FilWaiter *waiter, struct timespec *ts, PyObject *timeout_exc)
{
    int err;

    if (fil_waiter_signaled(waiter))
    {
        /* Signaled before we ever parked, so nothing can have been thrown
         * into us in the meantime: no wait, no unwind. */
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

        /* Signaled. A signal handler run by PyErr_CheckSignals() above can
         * have raised in the same round trip, so report it the same way the
         * greenlet path does rather than handing back a hand-over the caller
         * is about to abandon. */
        return PyErr_Occurred() ? FIL_WAITER_SIGNALED_UNWIND : 0;
    }

    waiter->gl = PyGreenlet_GetCurrent();
    if (waiter->gl == NULL)
    {
        /* Only fails once the interpreter is finalizing ("greenlet is being
         * finalized"), and parking with a NULL gl would park forever: a
         * signaler skips a waiter it cannot switch into.  Bail out with the
         * exception greenlet set, as the re-park below does. */
        return -1;
    }

    /* Publish the parked state under waiter_lock. A signaler (possibly on
     * another thread, possibly WITHOUT the GIL -- e.g. the io thread) either
     * observes WAITING+gl and enqueues our wakeup, or wins the race by
     * setting SIGNALED first, which we observe here before parking. */
    for (;;)
    {
        PyGreenlet *gl;
        int reservation_ours;

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
        /* Reserve the reference the wakeup event will own (see the wakeup
         * contract).  Done here, with the GIL held, precisely so that an
         * off-GIL signaler never has to touch refcnt. */
        waiter->refcnt++;
        pthread_mutex_unlock(&(waiter->waiter_lock));

        if (ts != NULL && waiter->timeout_event == NULL)
        {
            waiter->refcnt++;
            fil_scheduler_add_event_ref(waiter->sched, ts, 0,
                                        (fil_event_cb_t)_fil_waiter_handle_timeout,
                                        waiter, &(waiter->timeout_event));
        }

        fil_scheduler_switch(waiter->sched);

        /* Signaled (or thrown into) before the deadline?  Take the timeout
         * event back out of the scheduler.  Leaving it queued would hold this
         * waiter -- and its slot in the timer heap -- for the rest of the
         * timeout, which for the 60s timeouts network clients like to use
         * means a wait that finished in a millisecond keeps its memory for a
         * minute.  del_event tells us whether we won the race against the
         * scheduler running it; if we did, the reference the event held is
         * ours to drop. */
        if (waiter->timeout_event != NULL &&
            fil_scheduler_del_event(waiter->sched, &(waiter->timeout_event)))
        {
            fil_waiter_decref(waiter);
        }

        /* Synchronization barrier: a signaler's last touch of this waiter
         * happens under waiter_lock (see fil_waiter_signal), and we can only
         * have resumed after it enqueued our switch from inside that critical
         * section. Taking the lock here therefore guarantees the signaler is
         * completely done with the waiter before we release our references /
         * potentially free it.
         *
         * It is also where we settle the wakeup: we are running again,
         * whether that was our wakeup switch, an expiring timeout or a throw
         * from another greenthread, so nothing may switch into us on this
         * waiter's behalf ever again. */
        pthread_mutex_lock(&(waiter->waiter_lock));

        err = fil_waiter_signaled(waiter) ? 1 : 0;

        /* No longer parked, and no longer switchable: a wakeup already in the
         * scheduler's hands (dequeued, callback about to run) sees this NULL
         * and does nothing. */
        waiter->flags &= ~FIL_WAITER_FLAGS_WAITING;
        gl = waiter->gl;
        waiter->gl = NULL;

        if (waiter->flags & FIL_WAITER_FLAGS_SWITCH_PENDING)
        {
            /* A wakeup was queued for us.  If it is still in the queue we take
             * it back -- and with it the reservation it owned.  If we lose
             * that race it is already running and will drop the reservation
             * itself. */
            if (waiter->switch_event != NULL &&
                fil_scheduler_del_event(waiter->sched, &(waiter->switch_event)))
            {
                waiter->flags &= ~FIL_WAITER_FLAGS_SWITCH_PENDING;
                reservation_ours = 1;
            }
            else
            {
                reservation_ours = 0;
            }
        }
        else
        {
            /* Nobody ever queued one (we were thrown into, or the enqueue
             * failed), so the reservation is still ours to drop. */
            reservation_ours = 1;
        }

        pthread_mutex_unlock(&(waiter->waiter_lock));

        Py_XDECREF(gl);
        if (reservation_ours)
        {
            fil_waiter_decref(waiter);
        }

        if (err)
        {
            /* Signaled: the wait is over.  We can have been thrown into by
             * the same wakeup (a kill() or an expiring Timeout that landed
             * after the signaler had already handed us the lock / item /
             * result), in which case the caller has to give it back before
             * the exception unwinds it out of here. */
            return PyErr_Occurred() ? FIL_WAITER_SIGNALED_UNWIND : 0;
        }

        if (PyErr_Occurred())
        {
            return -1;                      /* thrown into (kill, Timeout) */
        }

        /* Not signaled, nothing raised.  Something switched into us that was
         * not our wakeup -- most often a stale switch queued for a wait we
         * have already finished.  This is NOT a timeout, and must never be
         * reported as one: an untimed wait cannot time out at all.  Work out
         * whether our deadline has actually passed; if it has not (or there
         * is no deadline), go back to waiting. */
        if (ts != NULL)
        {
            struct timespec now;

            fil_timespec_now(&now);
            if (FIL_TIMESPEC_COMPARE(&now, ts, >=))
            {
                fil_set_timeout_exc(timeout_exc);
                return -ETIMEDOUT;
            }
        }

        /* Re-park.  Check signals first so a ^C during a spurious wakeup
         * still gets out (the classic-thread path above does the same). */
        if (PyErr_CheckSignals())
        {
            return -1;
        }

        waiter->gl = PyGreenlet_GetCurrent();
        if (waiter->gl == NULL)
        {
            return -1;
        }
    }
}

/*
 * Wake the waiter (common implementation).
 *
 * 'have_gil' says whether the CALLER holds the GIL:
 *   - fil_waiter_signal()       -- caller holds the GIL (all Python-driven
 *     signalers: the locking / queue / message primitives, thread-pool
 *     workers, socket teardown).
 *   - fil_waiter_signal_nogil() -- caller does NOT hold the GIL (the io
 *     thread's event callbacks).
 *
 * Either kind of caller may signal a TIMED waiter. That was not always true:
 * timed waiters used to be resumed through fil_scheduler_gl_switch(), which
 * touches a refcount and therefore demanded the GIL, so off-GIL signalers were
 * restricted to untimed waits. The wakeup now goes through
 * _fil_waiter_queue_switch() in every case, carrying the waiter and the
 * reservation the parked greenthread took for it (see the wakeup contract), so
 * no path here performs a Python API call or touches a refcount. The io thread
 * relies on this: a socket with settimeout() set parks on the cached
 * edge-triggered path with a deadline, and the io thread signals it off-GIL
 * exactly as it does an untimed one. The deadline itself is never the io
 * thread's business -- it is armed on, and fires from, the parked
 * greenthread's own scheduler timer (see fil_waiter_wait).
 *
 * Wakeup mechanics:
 *   - The wakeup switch is enqueued with a BORROWED greenlet reference; see
 *     _fil_waiter_switch_event_cb for why that is safe. For a TIMED waiter the
 *     timeout event can resume the greenlet concurrently, which is settled
 *     under waiter_lock in fil_waiter_wait: whichever side resumes first
 *     clears WAITING and gl there, and the loser observes !waiting here and
 *     returns without queueing anything.
 *   - For OS-thread waiters (sched == NULL) a GIL-holding caller drops the
 *     GIL around the condvar signal: the woken thread almost always needs
 *     the GIL next, and this voluntary release is a scheduling point that
 *     matters a lot for lock-heavy multi-thread workloads (e.g. logging from
 *     thread-pool workers under a monkey-patched hub).
 */
static inline void _fil_waiter_signal_common(FilWaiter *waiter, int have_gil)
{
    PyFilScheduler *sched;
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

    if (waiter->gl != NULL)
    {
        /* Wake the waiting greenlet by enqueuing a switch onto its home
         * scheduler; the scheduler greenlet performs the actual switch on its
         * own thread (never here). Enqueue while still holding waiter_lock so
         * the resumed greenlet's barrier (see fil_waiter_wait) cannot
         * complete -- and free the waiter -- until we are done with it.
         *
         * Works with or without the GIL: the event carries the waiter and the
         * reservation the parked greenthread made for it, so nothing here
         * touches a refcount.  See the wakeup contract above. */
        _fil_waiter_queue_switch(waiter, sched);
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

/*
 * Pass a signal we cannot use on to the next waiter, keeping the exception
 * that is unwinding us pending (see FIL_WAITER_SIGNALED_UNWIND).  Signaling
 * can raise -- fil_scheduler_add_event_ref() allocates -- and an exception
 * set here would replace the one the caller is propagating, so hold it out of
 * the way for the duration.
 */
static inline void _fil_waiterlist_signal_first_keep_exc(FilWaiterList *waiter_list)
{
    PyObject *exc_type, *exc_value, *exc_tb;

    PyErr_Fetch(&exc_type, &exc_value, &exc_tb);
    _fil_waiterlist_signal_first(waiter_list);
    PyErr_Restore(exc_type, exc_value, exc_tb);
}

#define fil_waiterlist_signal_first_keep_exc(waiter_list) \
    _fil_waiterlist_signal_first_keep_exc(&(waiter_list))

#endif /* __FIL_WAITER_H__ */
