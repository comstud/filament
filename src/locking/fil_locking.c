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

#define __FIL_BUILDING_LOCKING__
#include "core/filament.h"
#include "locking/fil_cond.h"
#include "locking/fil_lock.h"
#include "locking/fil_semaphore.h"

PyDoc_STRVAR(_fil_locking_module_doc, "Filament _filament.locking module.");
static PyMethodDef _fil_locking_module_methods[] = {
    { NULL, },
};

/****************/

PyMODINIT_FUNC
initlocking(void)
{
    PyObject *m;

    m = Py_InitModule3("_filament.locking", _fil_locking_module_methods, _fil_locking_module_doc);
    if (m == NULL)
    {
        return;
    }

    if (fil_lock_type_init(m) < 0 ||
            fil_cond_type_init(m) < 0 ||
            fil_semaphore_type_init(m) < 0)
    {
        return;
    }

    return;
}
