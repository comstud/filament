#ifndef __FIL_SEMAPHORE_H__
#define __FIL_SEMAPHORE_H__

#include <Python.h>

typedef struct _pyfil_semaphore PyFilSemaphore;

int fil_semaphore_type_init(PyObject *module);

#endif /* __FIL_SEMAPHORE_H__ */
