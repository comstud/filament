#ifndef __FIL_COND_H__
#define __FIL_COND_H__

#include <Python.h>
#include <sys/time.h>

typedef struct _pyfil_cond PyFilCond;

int fil_cond_type_init(PyObject *module);

#endif /* __FIL_COND_H__ */
