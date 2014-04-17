#ifndef __FIL_MESSAGE_H__
#define __FIL_MESSAGE_H__

#include <Python.h>
#include <sys/time.h>

typedef struct _pyfil_message PyFilMessage;

int fil_message_type_init(PyObject *module);
PyFilMessage *fil_message_alloc(void);
int fil_message_send(PyFilMessage *message, PyObject *result);
int fil_message_send_exception(PyFilMessage *message, PyObject *exc_type, PyObject *exc_value, PyObject *exc_tb);
PyObject *fil_message_wait(PyFilMessage *message, struct timespec *ts);


#endif /* __FIL_MESSAGE_H__ */
