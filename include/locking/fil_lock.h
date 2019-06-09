#ifndef __FIL_LOCKING_LOCK_H__
#define __FIL_LOCKING_LOCK_H__

#include <Python.h>
#include <sys/time.h>

typedef struct _pyfil_lock PyFilLock;
typedef struct _pyfil_rlock PyFilRLock;

int fil_lock_type_init(PyObject *module);

PyFilLock *fil_lock_alloc(void);
int fil_lock_acquire(PyFilLock *lock, int blocking, struct timespec *ts);
int fil_lock_release(PyFilLock *lock);

PyFilRLock *fil_rlock_alloc(void);
int fil_rlock_acquire(PyFilRLock *rlock, int blocking, struct timespec *ts);
int fil_rlock_release(PyFilRLock *rlock);

#endif /* __FIL_LOCKING_LOCK_H__ */
