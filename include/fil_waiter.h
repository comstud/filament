#ifndef __FIL_WAITER_H__
#define __FIL_WAITER_H__

#include <Python.h>
#include <sys/time.h>
#include <stddef.h>
#include <pthread.h>
#include <greenlet.h>
#include "fil_scheduler.h"

static inline void _workaround_unused_greenlet_api(void) { (void)_PyGreenlet_API; }

typedef struct _pyfil_waiter PyFilWaiter;
typedef struct _waiterlist WaiterList;

int fil_waiter_type_init(PyObject *module);
PyFilWaiter *fil_waiter_alloc(void);
int fil_waiter_wait(PyFilWaiter *waiter, struct timespec *ts);
void fil_waiter_signal(PyFilWaiter *waiter);

struct _waiterlist {
    WaiterList *prev;
    WaiterList *next;
};

struct _pyfil_waiter {
    PyObject_HEAD
    PyFilScheduler *sched;
    PyGreenlet *gl;
    pthread_mutex_t waiter_lock;
    pthread_cond_t waiter_cond;
    int signaled;

    WaiterList waiter_list;
};

#define waiterlist_init(head) \
    _waiterlist_init(&(head))

#define waiterlist_add_waiter_tail(head, waiter) \
    _waiterlist_add_tail(&(head), &((waiter)->waiter_list))

#define waiterlist_remove_waiter(waiter) \
    _waiterlist_del(&((waiter)->waiter_list), 1)

#define waiterlist_entry(cur) \
    (PyFilWaiter *)((char *)cur - offsetof(PyFilWaiter, waiter_list))

#define waiterlist_iterate(cur, head) \
    for(cur=(head).next;cur != &(head);cur=cur->next)

#define waiterlist_iterate_safe(cur, head, tmp) \
    for(cur=(head).next,tmp=cur->next;cur != &(head);cur=tmp;tmp=cur->next)

#define _waiterlist_iterate_and_remove(cur, head, tmp) \
    for(cur=(head)->next,tmp=cur->next,_waiterlist_del(cur, 0);cur != head;cur=tmp,tmp=cur->next)

#define waiterlist_iterate_and_remove(cur, head, tmp) \
    _waiterlist_iterate_and_remove(cur, &(head), tmp)

#define waiterlist_signal_all(head) \
    _waiterlist_signal_all(&(head))

#define waiterlist_signal_first(head) \
    _waiterlist_signal_first(&(head))

#define waiterlist_empty(head) \
    (head.next == &(head))

static inline void _waiterlist_init(WaiterList *list)
{
    list->next = list;
    list->prev = list;
}

static inline void _waiterlist_add_tail(WaiterList *head, WaiterList *entry)
{
    WaiterList *prev = head->prev;

    entry->prev = prev;
    entry->next = head;
    head->prev = entry;
    prev->next = entry;
    Py_INCREF(waiterlist_entry(entry));
}

static inline void _waiterlist_del(WaiterList *entry, int decref)
{
    WaiterList *next = entry->next;
    WaiterList *prev = entry->prev;

    next->prev = prev;
    prev->next = next;
    if (decref)
    {
        Py_DECREF(waiterlist_entry(entry));
    }
}

static inline void _waiterlist_signal_all(WaiterList *head)
{
    WaiterList *wl, *tmp;
    _waiterlist_iterate_and_remove(wl, head, tmp) {
        PyFilWaiter *waiter = waiterlist_entry(wl);
        fil_waiter_signal(waiter);
        Py_DECREF(waiter);
    }
}

static inline void _waiterlist_signal_first(WaiterList *head)
{
    WaiterList *wl = head->next;
    if (wl != head) {
        PyFilWaiter *waiter = waiterlist_entry(wl);
        _waiterlist_del(wl, 0);
        fil_waiter_signal(waiter);
        Py_DECREF(waiter);
    }
}

#endif /* __FIL_WAITER_H__ */
