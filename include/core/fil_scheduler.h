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

/* Events live in one of two structures, picked by how they were scheduled:
 *
 *   - "wake up now" events (ts == NULL at add time) go on a FIFO list, which
 *     keeps them O(1) to push and pop and preserves the relative order of
 *     sleep(0)-style yields.  This is the hot path: every context switch
 *     queues one.
 *   - timed events go in a binary min-heap keyed on the deadline, so arming
 *     a timeout is O(log n) instead of a linear walk of a sorted list, and
 *     the earliest deadline is entry 0.
 *
 * An event knows which one it is in by its heap_idx: FIL_SCHED_HEAP_NOT_QUEUED
 * means "on the FIFO", anything else is its slot in the heap array.
 */
#define FIL_SCHED_HEAP_NOT_QUEUED ((size_t)-1)

typedef struct _pyfil_sched_event
{
#define FIL_EVENT_COMPARE(__x, __y, __cmp)                 \
     FIL_TIMESPEC_COMPARE(&(__x)->ts, &(__y)->ts, __cmp)
    struct timespec ts;
    uint32_t flags; /* defined in fil_scheduler.h */
    fil_event_cb_t cb;
    void *cb_arg;
    /* Optional back-pointer to the owner's handle for this event (a Timer's
     * 'event' field, say).  The scheduler stores the node there when the
     * event is queued and NULLs it -- always under sched_lock -- the instant
     * the event leaves the queue, whether that is because it became ready or
     * because the owner cancelled it.  An owner can therefore test its handle
     * under the lock and know whether the node is still its to remove; see
     * fil_scheduler_add_event_ref() / fil_scheduler_del_event(). */
    FilSchedEvent **owner_ref;
    /* Slot in the timer heap, or FIL_SCHED_HEAP_NOT_QUEUED when this event is
     * on the immediate FIFO instead. */
    size_t heap_idx;
    /* FIFO links; unused (and stale) once the event leaves the FIFO. */
    FilSchedEvent *prev;
    FilSchedEvent *next;
} FilSchedEvent;

typedef struct
{
    FilSchedEvent *head;
    FilSchedEvent *tail;
} FilSchedEventList;

/* Min-heap of timed events, ordered by deadline; entries[0] is the earliest.
 * Each event caches its own index so cancelling one is O(log n) rather than a
 * search. */
typedef struct
{
    FilSchedEvent **entries;
    size_t len;
    size_t capacity;
} FilSchedTimerHeap;

/* Cap on the per-scheduler freelist of FilSchedEvent structs (see
 * _scheduler_add_event / _sched_main).  Sized so even high-concurrency
 * workloads (~1000 greenlets with an event in flight each) never touch malloc
 * in steady state, while keeping the worst-case cached memory small
 * (2048 * sizeof(FilSchedEvent) ~= 112KB). */
#define FIL_SCHED_EVENT_FREELIST_MAX 2048

typedef struct _pyfil_scheduler
{
    PyObject_HEAD
    PyGreenlet *greenlet;
    PyThreadState *thread_state;
    pthread_mutex_t sched_lock;
    pthread_cond_t sched_cond;
    /* Ready-now events, in the order they were queued. */
    FilSchedEventList immediate;
    /* Events waiting for a deadline, earliest first. */
    FilSchedTimerHeap timers;
    /* Freelist of FilSchedEvent nodes, protected by sched_lock.  Every
     * greenlet context switch allocates (and then frees) at least one
     * event, so recycling nodes removes a malloc/free pair from the
     * hottest path in the scheduler.  Bounded by
     * FIL_SCHED_EVENT_FREELIST_MAX. */
    FilSchedEvent *event_freelist;
    int event_freelist_len;
    PyObject *system_exceptions;
    int running;
    int aborting;
    /* OS thread that owns this scheduler. A scheduler and its greenlet are
     * bound to the thread that created them; switching to a greenlet from a
     * different thread is illegal in greenlet and crashes. We record the
     * owner here so cross-thread misuse can be rejected instead of crashing.
     */
    unsigned long thread_id;
} PyFilScheduler;

#ifdef __FIL_BUILDING_CORE__

typedef struct _pyfilcore_capi PyFilCore_CAPIObject;

int fil_scheduler_init(PyObject *module, PyFilCore_CAPIObject *capi);
PyFilScheduler *fil_scheduler_get(int create);
int fil_scheduler_add_event(PyFilScheduler *sched, struct timespec *ts, uint32_t event_flags, fil_event_cb_t cb, void *cb_arg);
int fil_scheduler_add_event_ref(PyFilScheduler *sched, struct timespec *ts, uint32_t event_flags, fil_event_cb_t cb, void *cb_arg, FilSchedEvent **owner_ref);
int fil_scheduler_del_event(PyFilScheduler *sched, FilSchedEvent **owner_ref);
int fil_scheduler_switch(PyFilScheduler *sched);
int fil_scheduler_gl_switch(PyFilScheduler *sched, struct timespec *ts, PyGreenlet *greenlet);
PyGreenlet *fil_scheduler_greenlet(PyFilScheduler *sched);

#else

static PyFilScheduler *(*fil_scheduler_get)(int create);
static int (*fil_scheduler_add_event)(PyFilScheduler *sched, struct timespec *ts, uint32_t event_flags, fil_event_cb_t cb, void *cb_arg);
static int (*fil_scheduler_add_event_ref)(PyFilScheduler *sched, struct timespec *ts, uint32_t event_flags, fil_event_cb_t cb, void *cb_arg, FilSchedEvent **owner_ref);
static int (*fil_scheduler_del_event)(PyFilScheduler *sched, FilSchedEvent **owner_ref);
static int (*fil_scheduler_switch)(PyFilScheduler *sched);
static int (*fil_scheduler_gl_switch)(PyFilScheduler *sched, struct timespec *ts, PyGreenlet *greenlet);
static PyGreenlet *(*fil_scheduler_greenlet)(PyFilScheduler *sched);

#endif

#endif /* __FIL_CORE_FIL_SCHEDULER_H__ */
