#ifndef __FIL_CORE_FIL_EXCEPTIONS_H__
#define __FIL_CORE_FIL_EXCEPTIONS_H__

#ifdef __FIL_BUILDING_CORE__

typedef struct _pyfilcore_capi PyFilCore_CAPIObject;

/* exceptions */
int fil_exceptions_init(PyObject *module, PyFilCore_CAPIObject *capi);
extern PyObject *PyFil_TimeoutExc;

#else

static PyObject *PyFil_TimeoutExc;

#endif

#endif /* __FIL_CORE_FIL_EXCEPTIONS_H__ */
