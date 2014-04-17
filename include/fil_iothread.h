#ifndef __FIL_IOTHREAD_H__
#define __FIL_IOTHREAD_H__

#include <Python.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/socket.h>

typedef struct _pyfil_iothread PyFilIOThread;

int fil_iothread_type_init(PyObject *module);
PyFilIOThread *fil_iothread_get(void);

int fil_iothread_read_ready(PyFilIOThread *iothr, int fd, struct timespec *timeout);
int fil_iothread_write_ready(PyFilIOThread *iothr, int fd, struct timespec *timeout);

ssize_t fil_iothread_read(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout);
ssize_t fil_iothread_write(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout);

/* Socket calls */
int fil_iothread_connect(PyFilIOThread *iothr, int fd, struct sockaddr *address, socklen_t address_len, struct timespec *timeout);

ssize_t fil_iothread_recv(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout);
ssize_t fil_iothread_recvfrom(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct sockaddr *address, socklen_t *address_len, struct timespec *timeout);
ssize_t fil_iothread_recvmsg(PyFilIOThread *iothr, int fd, struct msghdr *message, int flags, struct timespec *timeout);
ssize_t fil_iothread_send(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout);

ssize_t fil_iothread_sendto(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct sockaddr *address, socklen_t address_len, struct timespec *timeout);
ssize_t fil_iothread_sendmsg(PyFilIOThread *iothr, int fd, struct msghdr *message, int flags, struct timespec *timeout);

#endif /* __FIL_IOTHREAD_H__ */
