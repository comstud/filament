#ifndef __FIL_CORE_MESSAGE_H__
#define __FIL_CORE_MESSAGE_H__

#include <Python.h>
#include <sys/time.h>

typedef struct _pyfil_message PyFilMessage;

#ifdef __FIL_CORE__

typedef struct _pyfilcore_capi PyFilCore_CAPIObject;

int fil_message_init(PyObject *module, PyFilCore_CAPIObject *capi);
PyFilMessage *fil_message_alloc(void);
int fil_message_send(PyFilMessage *message, PyObject *result);
int fil_message_send_exception(PyFilMessage *message, PyObject *exc_type, PyObject *exc_value, PyObject *exc_tb);
PyObject *fil_message_wait(PyFilMessage *message, struct timespec *ts);

#endif

#endif /* __FIL_CORE_MESSAGE_H__ */
