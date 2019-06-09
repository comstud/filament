#ifndef __FIL_IO_H__
#define __FIL_IO_H__

#include "core/filament.h"
#include "io/fil_iothread.h"

#ifdef __FIL_BUILDING_IO__

int fil_io_init(PyObject *module);

#endif

#endif /* __FIL_IO_H__ */
