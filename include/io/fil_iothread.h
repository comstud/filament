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

/*
 * Eager-io request, handed to fil_iothread_wait_cached() by a caller that is
 * about to park on a socket.
 *
 * If 'buffer' is non-NULL the io thread performs the recv() (or send()) ITSELF,
 * on this caller-owned buffer, before it wakes the parked greenlet -- so the
 * wakeup hands back a COMPLETED TRANSFER rather than mere readiness and the
 * woken caller does not re-enter the kernel at all.  The win is not the syscall
 * count (it is the same syscall, just on another thread): on the calling thread
 * every recv/send is bracketed by Py_BEGIN/END_ALLOW_THREADS, i.e. a GIL drop
 * and reacquire, while the io thread holds no GIL and cycles nothing.
 *
 * No copy is added in either direction, and nothing is buffered: the io thread
 * makes exactly the call the caller would have made, on the caller's own
 * memory, which stays alive because the caller is parked until it returns.
 * That is what separates this from a speculative read-ahead or write-behind --
 * the greenthread never returns early, so send() keeps reporting synchronously
 * how many bytes reached the kernel.
 *
 * 'done' is set only if the io thread actually completed the call, in which
 * case 'result'/'errn' are its return value and errno.  If 'done' is 0 the
 * caller must retry the syscall itself exactly as it did before (spurious
 * edge, the io thread saw EAGAIN, or the wait ended by timeout/throw).
 *
 * The struct itself may live in the caller's frame: wait_cached only writes to
 * it AFTER the greenlet has resumed, on the greenlet's own thread.  What must
 * NOT live in the caller's frame is anything the IO THREAD writes -- classic
 * greenlets copy and restore the whole C stack across a switch, so such writes
 * would be discarded (see commit 30dcec8).  Hence the io thread writes only
 * into the heap-allocated FilIOFDWait and wait_cached copies out here.
 */
typedef struct _fil_io_eager
{
    void *buffer;       /* source/destination; NULL disables eager io */
    size_t buf_sz;
    int flags;          /* recv()/send() flags, passed through verbatim */
    int is_send;        /* 0 = recv into buffer, 1 = send from it.  Must match
                           the direction the caller is waiting on. */
    int done;           /* out: io thread performed the call */
    ssize_t result;     /* out: valid when done */
    int errn;           /* out: errno, valid when done && result < 0 */
} FilIOEagerIO;

#ifdef __FIL_BUILDING_IO__

int fil_iothread_init(PyObject *module);

PyFilIOThread *fil_iothread_get(void);

int fil_iothread_read_ready(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);
int fil_iothread_write_ready(PyFilIOThread *iothr, int fd, struct timespec *timeout, PyObject *timeout_exc);

int fil_iothread_wait_cached(PyFilIOThread *iothr, FilIOFDWait **cachep, int fd, int for_write, unsigned int seq, struct timespec *timeout, PyObject *timeout_exc, FilIOEagerIO *eager);
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

static int (*fil_iothread_wait_cached)(PyFilIOThread *iothr, FilIOFDWait **cachep, int fd, int for_write, unsigned int seq, struct timespec *timeout, PyObject *timeout_exc, FilIOEagerIO *eager);
static unsigned int (*fil_iothread_fdwait_seq)(FilIOFDWait *cache);
static void (*fil_iothread_fdwait_destroy)(FilIOFDWait *cache);

static ssize_t (*fil_iothread_read)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);
static ssize_t (*fil_iothread_write)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, struct timespec *timeout, PyObject *timeout_exc);

/* Socket calls */

static ssize_t (*fil_iothread_recv)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);
static ssize_t (*fil_iothread_send)(PyFilIOThread *iothr, int fd, void *buffer, size_t buf_sz, int flags, struct timespec *timeout, PyObject *timeout_exc);


#endif

#endif /* __FIL_IO_IOTHREAD_H__ */
