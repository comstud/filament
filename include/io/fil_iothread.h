#ifndef __FIL_IO_IOTHREAD_H__
#define __FIL_IO_IOTHREAD_H__

#include "core/filament.h"
#include <sys/socket.h>

typedef struct _pyfil_iothread PyFilIOThread;

#ifdef __FIL_BUILDING_IO__

int fil_iothread_init(PyObject *module);

PyFilIOThread *fil_iothread_get(void);

int fil_iothread_read_ready(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);
int fil_iothread_write_ready(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);

ssize_t fil_iothread_read(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);
ssize_t fil_iothread_write(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);

/* Socket calls */
int fil_iothread_connect(PyFilIOThread *iothr, int fd, struct sockaddr *address, socklen_t address_len, struct timespec *timeout, PyObject *timeout_exc);

ssize_t fil_iothread_recv(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);
ssize_t fil_iothread_recvfrom(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct sockaddr *address, socklen_t *address_len, struct timespec *timeout, PyObject *timeout_exc);
ssize_t fil_iothread_recvmsg(PyFilIOThread *iothr, int fd, struct msghdr *message, int flags, struct timespec *timeout, PyObject *timeout_exc);
ssize_t fil_iothread_send(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);

ssize_t fil_iothread_sendto(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct sockaddr *address, socklen_t address_len, struct timespec *timeout, PyObject *timeout_exc);
ssize_t fil_iothread_sendmsg(PyFilIOThread *iothr, int fd, struct msghdr *message, int flags, struct timespec *timeout, PyObject *timeout_exc);

#else

static PyTypeObject *PyFilIOThread_Type;

static PyFilIOThread *(*fil_iothread_get)(void);

static int (*fil_iothread_read_ready)(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);
static int (*fil_iothread_write_ready)(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);

static ssize_t (*fil_iothread_read)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);
static ssize_t (*fil_iothread_write)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);

/* Socket calls */
static int (*fil_iothread_connect)(PyFilIOThread *iothr, int fd, struct sockaddr *address, socklen_t address_len, struct timespec *timeout, PyObject *timeout_exc);

static ssize_t (*fil_iothread_recv)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);
static ssize_t (*fil_iothread_recvfrom)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct sockaddr *address, socklen_t *address_len, struct timespec *timeout, PyObject *timeout_exc);
static ssize_t (*fil_iothread_recvmsg)(PyFilIOThread *iothr, int fd, struct msghdr *message, int flags, struct timespec *timeout, PyObject *timeout_exc);
static ssize_t (*fil_iothread_send)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);

static ssize_t (*fil_iothread_sendto)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct sockaddr *address, socklen_t address_len, struct timespec *timeout, PyObject *timeout_exc);
static ssize_t (*fil_iothread_sendmsg)(PyFilIOThread *iothr, int fd, struct msghdr *message, int flags, struct timespec *timeout, PyObject *timeout_exc);

#endif

#endif /* __FIL_IO_IOTHREAD_H__ */
