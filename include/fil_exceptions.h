#ifndef __FIL_EXCEPTIONS_H__
#define __FIL_EXCEPTIONS_H__

#include <Python.h>

extern PyObject *PyFil_TimeoutExc;

int fil_exceptions_init(PyObject *module);

#endif /* __FIL_EXCEPTIONS_H__ */
