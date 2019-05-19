#ifndef __FIL_QUEUE_H__
#define __FIL_QUEUE_H__

#include <Python.h>
#include <sys/time.h>

typedef struct _pyfil_queue PyFilQueue;

int fil_queue_module_init(PyObject *module);

#endif /* __FIL_QUEUE_H__ */
