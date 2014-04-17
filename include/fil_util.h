#ifndef __FIL_UTIL_H__
#define __FIL_UTIL_H__


#include <Python.h>
#include <sys/time.h>

#define TIMESPEC_COMPARE(__x, __y, __cmp)                 \
        (((__x)->tv_sec == (__y)->tv_sec) ?          \
                 ((__x)->tv_nsec __cmp (__y)->tv_nsec) :  \
                 ((__x)->tv_sec __cmp (__y)->tv_sec))

int fil_timeoutobj_to_timespec(PyObject *timeoutobj, struct timespec *ts_buf, struct timespec **ts_ret);
uint64_t fil_get_ident(void);
PyObject *fil_create_module(char *name);

#endif /* __FIL_UTIL_H__ */
