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
    #define FIL_WAITER_FLAGS_SIGNALED   0x001
    #define FIL_WAITER_FLAGS_WAITING    0x002
    unsigned int flags;
    unsigned int refcnt;
    pthread_mutex_t waiter_lock;
    pthread_cond_t waiter_cond;
    FilWaiterList waiter_list;
};

static inline FilWaiter *fil_waiter_alloc(void)
{
    FilWaiter *waiter = malloc(sizeof(FilWaiter));
    if (waiter == NULL) {
        PyErr_SetString(PyExc_MemoryError, "failed to alloc FilWaiter");
    } else {
        waiter->sched = NULL;
        waiter->gl = NULL;
        waiter->flags = 0;
        waiter->refcnt = 1;
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
        pthread_mutex_destroy(&(waiter->waiter_lock));
        pthread_cond_destroy(&(waiter->waiter_cond));
        free(waiter);
    }
}


static inline void _fil_waiter_handle_timeout(PyFilScheduler *sched, FilWaiter *waiter)
{
    if (waiter->gl)
    {
        fil_scheduler_gl_switch(sched, NULL, waiter->gl);
    }
    fil_waiter_decref(waiter);
}

static inline int fil_waiter_wait(FilWaiter *waiter, struct timespec *ts, PyObject *timeout_exc)
{
    if (fil_waiter_signaled(waiter))
    {
        return 0;
    }

    fil_waiter_set_waiting(waiter);

    Py_XSETREF(waiter->sched, fil_scheduler_get(0));
    if (waiter->sched == NULL)
    {
        PyThreadState *thr_state;
        int err;

        for(;;)
        {
            thr_state = PyEval_SaveThread();
            pthread_mutex_lock(&(waiter->waiter_lock));

            /* race with GIL unlocked? */
            if (fil_waiter_signaled(waiter))
            {
                pthread_mutex_unlock(&(waiter->waiter_lock));
                PyEval_RestoreThread(thr_state);
                break;
            }

            err = fil_pthread_cond_wait_min(&(waiter->waiter_cond),
                                            &(waiter->waiter_lock), ts);

            pthread_mutex_unlock(&(waiter->waiter_lock));
            PyEval_RestoreThread(thr_state);

            if (fil_waiter_signaled(waiter))
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
                return -1;
            }
        }

        return 0;
    }

    waiter->gl = PyGreenlet_GetCurrent();

    if (ts != NULL)
    {
        waiter->refcnt++;
        fil_scheduler_add_event(waiter->sched, ts, 0,
                                (fil_event_cb_t)_fil_waiter_handle_timeout, waiter);
    }

    fil_scheduler_switch(waiter->sched);

    Py_CLEAR(waiter->gl);

    if (!fil_waiter_signaled(waiter))
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

static inline void fil_waiter_signal(FilWaiter *waiter)
{
    if (fil_waiter_signaled(waiter))
    {
        return;
    }

    fil_waiter_set_signaled(waiter);

    if (!fil_waiter_waiting(waiter))
    {
        return;
    }

    if (waiter->sched == NULL)
    {
        /* We don't necessarily need to release the GIL but this
         * might be better to wake up other threads sooner
         */
        PyThreadState *thr_state = PyEval_SaveThread();

        pthread_mutex_lock(&(waiter->waiter_lock));
        pthread_cond_signal(&(waiter->waiter_cond));
        pthread_mutex_unlock(&(waiter->waiter_lock));

        PyEval_RestoreThread(thr_state);
        return;
    }

    if (waiter->gl != NULL)
    {
        fil_scheduler_gl_switch(waiter->sched, NULL, waiter->gl);
    }

    return;
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
