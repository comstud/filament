#ifndef __FIL_CORE_FILAMENT_H__
#define __FIL_CORE_FILAMENT_H__

#include <Python.h>
#include <structmember.h>
/*
 * PyThread_get_thread_ident() (used by fil_get_ident in fil_util.h and by the
 * scheduler's owner-thread guard) lives in pythread.h.  Python 3's Python.h
 * pulls this in transitively, but Python 2.7's does not -- so include it
 * explicitly to keep the Py2.7 build working.
 */
#include <pythread.h>
#if PY_VERSION_HEX >= 0x030b0000
/* PyLongObject internals moved out of the public headers in 3.11+
 * (top-level longintrepr.h was dropped; only cpython/longintrepr.h
 * remains). */
#include <cpython/longintrepr.h>
#else
#include <longintrepr.h>
#endif
#include <greenlet.h>

#include <errno.h>
#include <pthread.h>
#include <stddef.h>
#include <stdlib.h>
#include <sys/time.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>
#include "pyversion.h"
#include "core/fil_exceptions.h"
#include "core/fil_fifoq.h"
#include "core/fil_message.h"
#include "core/fil_scheduler.h"
#include "core/fil_thrpool.h"
#include "core/fil_util.h"
#include "core/fil_waiter.h"
#include "core/fil_wfifoq.h"

#define FILAMENT_CORE_MODULE_NAME "_filament.core"
#define FILAMENT_CORE_CAPI_NAME "CAPI"
#define FILAMENT_CORE_CAPSULE_NAME (FILAMENT_CORE_MODULE_NAME "." FILAMENT_CORE_CAPI_NAME)

typedef struct _pyfilcore_capi
{
    PyTypeObject *filament_type;

    PyFilament *(*filament_alloc)(PyObject *method, PyObject *args, PyObject *kwargs);

    /* exceptions */
    PyObject *timeout_exc;

    /* scheduler */
    PyFilScheduler *(*fil_scheduler_get)(int create);
    int (*fil_scheduler_add_event)(PyFilScheduler *sched, struct timespec *ts, uint32_t flags, fil_event_cb_t cb, void *cb_arg);
    int (*fil_scheduler_switch)(PyFilScheduler *sched);
    int (*fil_scheduler_gl_switch)(PyFilScheduler *sched, struct timespec *ts, PyGreenlet *greenlet);
    PyGreenlet *(*fil_scheduler_greenlet)(PyFilScheduler *sched);
    /* Append new entries HERE, at the end.  setuptools does not track header
     * dependencies, so an incremental build can leave a sibling extension
     * compiled against an older copy of this struct; keeping existing slots
     * at fixed offsets turns that into a missing feature rather than a call
     * through a shifted pointer. */
    int (*fil_scheduler_add_event_ref)(PyFilScheduler *sched, struct timespec *ts, uint32_t flags, fil_event_cb_t cb, void *cb_arg, FilSchedEvent **owner_ref);
    int (*fil_scheduler_del_event)(PyFilScheduler *sched, FilSchedEvent **owner_ref);
} PyFilCore_CAPIObject;

#ifdef __FIL_BUILDING_CORE__

extern PyTypeObject *PyFilament_Type;
PyFilament *filament_alloc(PyObject *method, PyObject *args, PyObject *kwargs);

#else

static PyFilCore_CAPIObject *_PY_FIL_CORE_API;

static PyTypeObject *PyFilament_Type;

static PyFilament *(*filament_alloc)(PyObject *method, PyObject *args, PyObject *kwargs);

static inline int PyFilCore_Import(void)
{
    PyObject *m;

    PyGreenlet_Import();

    if (_PY_FIL_CORE_API != NULL)
    {
        return 0;
    }

    m = PyImport_ImportModule(FILAMENT_CORE_MODULE_NAME);
    if (m == NULL)
    {
        return -1;
    }

    _PY_FIL_CORE_API = PyCapsule_Import(FILAMENT_CORE_CAPSULE_NAME, 1);
    if (_PY_FIL_CORE_API == NULL)
    {
        return -1;
    }

    PyFilament_Type = _PY_FIL_CORE_API->filament_type;
    filament_alloc = _PY_FIL_CORE_API->filament_alloc;
    PyFil_TimeoutExc = _PY_FIL_CORE_API->timeout_exc;
    fil_scheduler_get = _PY_FIL_CORE_API->fil_scheduler_get;
    fil_scheduler_add_event = _PY_FIL_CORE_API->fil_scheduler_add_event;
    fil_scheduler_add_event_ref = _PY_FIL_CORE_API->fil_scheduler_add_event_ref;
    fil_scheduler_del_event = _PY_FIL_CORE_API->fil_scheduler_del_event;
    fil_scheduler_switch = _PY_FIL_CORE_API->fil_scheduler_switch;
    fil_scheduler_gl_switch = _PY_FIL_CORE_API->fil_scheduler_gl_switch;
    fil_scheduler_greenlet = _PY_FIL_CORE_API->fil_scheduler_greenlet;

    return 0;
}

#endif

#endif /* __FIL_CORE_FILAMENT_H__ */
