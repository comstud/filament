#ifndef __FIL_SCHEDULER_H__
#define __FIL_SCHEDULER_H__

#include <Python.h>
#include <greenlet.h>
#include <sys/time.h>

typedef struct _pyfil_scheduler PyFilScheduler;
typedef void (*fil_event_cb_t)(PyFilScheduler *sched, void *cb_arg);

#define EVENT_FLAGS_DONTBLOCK_THREADS   0x00000001

int fil_scheduler_type_init(PyObject *module);
PyFilScheduler *fil_scheduler_get(int create);
int fil_scheduler_add_event(PyFilScheduler *sched, struct timespec *ts, uint32_t event_flags, fil_event_cb_t cb, void *cb_arg);
void fil_scheduler_switch(PyFilScheduler *sched);
void fil_scheduler_gl_switch(PyFilScheduler *sched, struct timespec *ts, PyGreenlet *greenlet);
PyGreenlet *fil_scheduler_greenlet(PyFilScheduler *sched);

#endif /* __FIL_SCHEDULER_H__ */
