#ifndef __FIL_IO_IOTHREAD_H__
#define __FIL_IO_IOTHREAD_H__

#include "core/filament.h"
#include <sys/socket.h>

typedef struct _pyfil_iothread PyFilIOThread;

/*
 * Cached edge-triggered fd-readiness waiter (see fil_iothread.c).  One of
 * these is owned per (socket object, direction); the libevent event inside is
 * registered ONCE (EV_PERSIST|EV_ET) for the lifetime of the fd instead of
 * being added/deleted (two epoll_ctl calls + allocations) per blocked
 * operation.  Opaque outside fil_iothread.c.
 */
typedef struct _fil_io_fdwait FilIOFDWait;

#ifdef __FIL_BUILDING_IO__

int fil_iothread_init(PyObject *module);

PyFilIOThread *fil_iothread_get(void);

int fil_iothread_read_ready(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);
int fil_iothread_write_ready(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);

int fil_iothread_wait_cached(PyFilIOThread *iothr, FilIOFDWait **cachep, int fd, int for_write, unsigned int seq);
unsigned int fil_iothread_fdwait_seq(FilIOFDWait *cache);
void fil_iothread_fdwait_destroy(FilIOFDWait *cache);

ssize_t fil_iothread_read(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);
ssize_t fil_iothread_write(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);

/* Socket calls */

ssize_t fil_iothread_recv(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);
ssize_t fil_iothread_send(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);


#else

static PyTypeObject *PyFilIOThread_Type;

static PyFilIOThread *(*fil_iothread_get)(void);

static int (*fil_iothread_read_ready)(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);
static int (*fil_iothread_write_ready)(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);

static int (*fil_iothread_wait_cached)(PyFilIOThread *iothr, FilIOFDWait **cachep, int fd, int for_write, unsigned int seq);
static unsigned int (*fil_iothread_fdwait_seq)(FilIOFDWait *cache);
static void (*fil_iothread_fdwait_destroy)(FilIOFDWait *cache);

static ssize_t (*fil_iothread_read)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);
static ssize_t (*fil_iothread_write)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);

/* Socket calls */

static ssize_t (*fil_iothread_recv)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);
static ssize_t (*fil_iothread_send)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);


#endif

#endif /* __FIL_IO_IOTHREAD_H__ */
