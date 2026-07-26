#ifndef __FIL_LOCKING_LOCK_H__
#define __FIL_LOCKING_LOCK_H__

#include <Python.h>
#include <sys/time.h>

typedef struct _pyfil_lock PyFilLock;
typedef struct _pyfil_rlock PyFilRLock;

int fil_lock_type_init(PyObject *module);


PyFilRLock *fil_rlock_alloc(void);

#endif /* __FIL_LOCKING_LOCK_H__ */
