#ifndef __FIL_WAITER_H__
#define __FIL_WAITER_H__

#include <Python.h>
#include <sys/time.h>

typedef struct _pyfil_waiter PyFilWaiter;

int fil_waiter_type_init(PyObject *module);
PyFilWaiter *fil_waiter_alloc(void);
int fil_waiter_wait(PyFilWaiter *waiter, struct timespec *ts);
void fil_waiter_signal(PyFilWaiter *waiter, int gil_unlocked);


#endif /* __FIL_WAITER_H__ */
