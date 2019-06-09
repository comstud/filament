#ifndef __FIL_IO_H__
#define __FIL_IO_H__

#include "core/filament.h"
#include "io/fil_iothread.h"

#define FILAMENT_IO_MODULE_NAME "_filament.io"
#define FILAMENT_IO_CAPI_NAME "CAPI"
#define FILAMENT_IO_CAPSULE_NAME (FILAMENT_IO_MODULE_NAME "." FILAMENT_IO_CAPI_NAME)

typedef struct _pyfilio_capi
{
    PyTypeObject *PyFilIOThread_Type;

    PyFilIOThread *(*fil_iothread_get)(void);

    int (*fil_iothread_read_ready)(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);
    int (*fil_iothread_write_ready)(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);

    ssize_t (*fil_iothread_read)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);
    ssize_t (*fil_iothread_write)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);

/* Socket calls */
    int (*fil_iothread_connect)(PyFilIOThread *iothr, int fd, struct sockaddr *address, socklen_t address_len, struct timespec *timeout, PyObject *timeout_exc);

    ssize_t (*fil_iothread_recv)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);
    ssize_t (*fil_iothread_recvfrom)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct sockaddr *address, socklen_t *address_len, struct timespec *timeout, PyObject *timeout_exc);
    ssize_t (*fil_iothread_recvmsg)(PyFilIOThread *iothr, int fd, struct msghdr *message, int flags, struct timespec *timeout, PyObject *timeout_exc);
    ssize_t (*fil_iothread_send)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);

    ssize_t (*fil_iothread_sendto)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct sockaddr *address, socklen_t address_len, struct timespec *timeout, PyObject *timeout_exc);
    ssize_t (*fil_iothread_sendmsg)(PyFilIOThread *iothr, int fd, struct msghdr *message, int flags, struct timespec *timeout, PyObject *timeout_exc);

} PyFilIO_CAPIObject;

#ifdef __FIL_BUILDING_IO__

extern PyFilIO_CAPIObject *_PY_FIL_IO_API;
int fil_io_init(PyObject *module);

#define _FIL_COPY_IO_API(__name) _PY_FIL_IO_API->__name = __name

#else

static PyFilIO_CAPIObject *_PY_FIL_IO_API;

#define _FIL_COPY_IO_API(__name) __name = _PY_FIL_IO_API->__name

#endif

#define FIL_COPY_IO_API() do {                  \
    _FIL_COPY_IO_API(PyFilIOThread_Type);       \
    _FIL_COPY_IO_API(fil_iothread_get);         \
    _FIL_COPY_IO_API(fil_iothread_read_ready);  \
    _FIL_COPY_IO_API(fil_iothread_write_ready); \
    _FIL_COPY_IO_API(fil_iothread_read);        \
    _FIL_COPY_IO_API(fil_iothread_write);       \
    _FIL_COPY_IO_API(fil_iothread_connect);     \
    _FIL_COPY_IO_API(fil_iothread_recv);        \
    _FIL_COPY_IO_API(fil_iothread_recvfrom);    \
    _FIL_COPY_IO_API(fil_iothread_recvmsg);     \
    _FIL_COPY_IO_API(fil_iothread_send);        \
    _FIL_COPY_IO_API(fil_iothread_sendto);      \
    _FIL_COPY_IO_API(fil_iothread_sendmsg);     \
} while(0)

#ifndef __FIL_BUILDING_IO__

static inline int PyFilIO_Import(void)
{
    PyObject *m;

    PyGreenlet_Import();

    if (_PY_FIL_IO_API != NULL)
    {
        return 0;
    }

    m = PyImport_ImportModule(FILAMENT_IO_MODULE_NAME);
    if (m == NULL)
    {
        return -1;
    }

    _PY_FIL_IO_API = PyCapsule_Import(FILAMENT_IO_CAPSULE_NAME, 1);
    if (_PY_FIL_IO_API == NULL)
    {
        return -1;
    }

    FIL_COPY_IO_API();

    return 0;
}

#endif

#endif /* __FIL_IO_H__ */
