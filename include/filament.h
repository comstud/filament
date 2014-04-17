#ifndef __FILAMENT_H__
#define __FILAMENT_H__

#include <Python.h>

typedef struct _pyfilament PyFilament;

int filament_type_init(PyObject *module);
PyFilament *filament_alloc(PyObject *method, PyObject *args, PyObject *kwargs);

#endif /* __FILAMENT_H__ */
