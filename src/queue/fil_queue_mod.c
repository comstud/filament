/*
 * The MIT License (MIT): http://opensource.org/licenses/mit-license.php
 *
 * Copyright (c) 2019, Chris Behrens
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 *
 */

#define __FIL_BUILDING_QUEUE__
#include "queue/fil_queue.h"

PyObject *_FIL_QUEUE_EMPTY_ERROR = NULL;
PyObject *_FIL_QUEUE_FULL_ERROR = NULL;

PyDoc_STRVAR(_fil_queue_module_doc, "Filament queue module.");
static PyMethodDef _fil_queue_module_methods[] = {
    { NULL, },
};

/****************/

_FIL_MODULE_INIT_FN_NAME(queue)
{
    PyObject *m = NULL;
    PyObject *qm = NULL;

    if (PyFilCore_Import() < 0)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    _FIL_MODULE_SET(m, "_filament.queue", _fil_queue_module_methods, _fil_queue_module_doc);
    if (m == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    qm = PyImport_ImportModule(_FIL_PY_QUEUE_MODULE_NAME);
    if (qm == NULL)
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    _FIL_QUEUE_EMPTY_ERROR = PyObject_GetAttrString(qm, "Empty");
    if (_FIL_QUEUE_EMPTY_ERROR == NULL)
    {
        Py_DECREF(qm);
        return _FIL_MODULE_INIT_ERROR;
    }

    _FIL_QUEUE_FULL_ERROR = PyObject_GetAttrString(qm, "Full");
    if (_FIL_QUEUE_FULL_ERROR == NULL)
    {
        Py_CLEAR(_FIL_QUEUE_EMPTY_ERROR);
        Py_DECREF(qm);
        return _FIL_MODULE_INIT_ERROR;
    }

    Py_DECREF(qm);

    Py_INCREF(_FIL_QUEUE_EMPTY_ERROR);
    if (PyModule_AddObject(m, "Empty", _FIL_QUEUE_EMPTY_ERROR) < 0)
    {
        Py_DECREF(_FIL_QUEUE_EMPTY_ERROR);
        goto failure;
    }

    Py_INCREF(_FIL_QUEUE_FULL_ERROR);
    if (PyModule_AddObject(m, "Full", _FIL_QUEUE_FULL_ERROR) < 0)
    {
        Py_DECREF(_FIL_QUEUE_FULL_ERROR);
        goto failure;
    }

    if (fil_simplequeue_init(m))
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    if (fil_queue_init(m))
    {
        return _FIL_MODULE_INIT_ERROR;
    }

    return _FIL_MODULE_INIT_SUCCESS(m);

failure:
    Py_CLEAR(_FIL_QUEUE_EMPTY_ERROR);
    Py_CLEAR(_FIL_QUEUE_FULL_ERROR);
    return _FIL_MODULE_INIT_ERROR;
}
