/* 
 * The MIT License (MIT): http://opensource.org/licenses/mit-license.php
 * 
 * Copyright (c) 2013-2014, Chris Behrens
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

#include "fil_exceptions.h"


static int _inited = 0;
PyObject *PyFil_TimeoutExc;


int fil_exceptions_init(PyObject *module)
{
    PyObject *exc_mod;

    if (_inited)
        return 0;
        
    exc_mod = PyImport_ImportModule("filament.exc");
    if (exc_mod == NULL)
    {
        return -1;
    }

    PyFil_TimeoutExc = PyObject_GetAttrString(exc_mod, "Timeout");
    if (PyFil_TimeoutExc == NULL)
    {
        Py_DECREF(exc_mod);
        return -1;
    }

    Py_DECREF(exc_mod);
    _inited = 1;
    return 0;
}
