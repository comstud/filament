#ifndef __FIL_CORE_FIL_SCHEDULER_H__
#define __FIL_CORE_FIL_SCHEDULER_H__

#include "core/filament.h"

typedef struct _pyfil_scheduler PyFilScheduler;
typedef struct _pyfil_sched_event FilSchedEvent;
typedef void (*fil_event_cb_t)(PyFilScheduler *sched, void *cb_arg);

#define FIL_SCHED_EVENT_FLAGS_DONTBLOCK_THREADS   0x00000001

typedef struct
{
    PyGreenlet greenlet;
    PyFilScheduler *sched;
    PyFilMessage *message;
    PyObject *method;
    PyObject *method_args;
    PyObject *method_kwargs;
} PyFilament;

/* FIXME: Convert FilSchedEvents to a priority queue */
typedef struct _pyfil_sched_event
{
#define FIL_EVENT_COMPARE(__x, __y, __cmp)                 \
     FIL_TIMESPEC_COMPARE(&(__x)->ts, &(__y)->ts, __cmp)
    struct timespec ts;
    uint32_t flags; /* defined in fil_scheduler.h */
    fil_event_cb_t cb;
    void *cb_arg;
    FilSchedEvent *prev;
    FilSchedEvent *next;
} FilSchedEvent;

typedef struct
{
    FilSchedEvent *head;
    FilSchedEvent *tail;
} FilSchedEventList;

typedef struct _pyfil_scheduler
{
    PyObject_HEAD
    PyGreenlet *greenlet;
    PyThreadState *thread_state;
    pthread_mutex_t sched_lock;
    pthread_cond_t sched_cond;
    FilSchedEventList events;
    PyObject *system_exceptions;
    int running;
    int aborting;
} PyFilScheduler;

#ifdef __FIL_BUILDING_CORE__

typedef struct _pyfilcore_capi PyFilCore_CAPIObject;

int fil_scheduler_init(PyObject *module, PyFilCore_CAPIObject *capi);
PyFilScheduler *fil_scheduler_get(int create);
int fil_scheduler_add_event(PyFilScheduler *sched, struct timespec *ts, uint32_t event_flags, fil_event_cb_t cb, void *cb_arg);
int fil_scheduler_switch(PyFilScheduler *sched);
void fil_scheduler_gl_switch(PyFilScheduler *sched, struct timespec *ts, PyGreenlet *greenlet);
PyGreenlet *fil_scheduler_greenlet(PyFilScheduler *sched);

#else

PyFilScheduler *(*fil_scheduler_get)(int create);
static int (*fil_scheduler_add_event)(PyFilScheduler *sched, struct timespec *ts, uint32_t event_flags, fil_event_cb_t cb, void *cb_arg);
static int (*fil_scheduler_switch)(PyFilScheduler *sched);
static void (*fil_scheduler_gl_switch)(PyFilScheduler *sched, struct timespec *ts, PyGreenlet *greenlet);
static PyGreenlet *(*fil_scheduler_greenlet)(PyFilScheduler *sched);

#endif

#endif /* __FIL_CORE_FIL_SCHEDULER_H__ */
