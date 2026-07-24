#ifndef __FIL_CORE_PYVERSION_H__
#define __FIL_CORE_PYVERSION_H__

/*
 * Central Python 2 vs Python 3 compatibility shims for filament.
 *
 * This header is included (via core/filament.h) by every translation
 * unit.  <Python.h> and <greenlet.h> are expected to have been included
 * already, but we defensively include <Python.h> here so the version
 * macros are available.
 *
 * The guiding principle: keep the Python 2 build working "by
 * construction" -- every Py3 mapping is paired with a Py2 fallback and
 * selected purely on PY_MAJOR_VERSION / PY_VERSION_HEX.  Do not remove
 * the Python 2 branches.
 */

#include <Python.h>

/*
 * Selector macro.  Used both as `#if _FIL_PYTHON3` (evaluates to 0 when
 * undefined on Py2) and as `#ifdef _FIL_PYTHON3`.  Therefore it must be
 * *defined to 1* on Py3 and *left entirely undefined* on Py2.
 */
#if PY_MAJOR_VERSION >= 3
#define _FIL_PYTHON3 1
#endif

#if PY_MAJOR_VERSION >= 3

/*
 * ------------------------------------------------------------------
 * "String" compatibility.
 *
 * In this codebase every PyString_* use operates on raw byte buffers:
 *   - io read buffers (src/io/fil_io.c)
 *   - socket recv buffers (src/socket/fil_socket.c)
 *   - module / method-name identifiers (socket resolver setup)
 * On Python 2 these were `str` (i.e. bytes).  The byte-buffer intent
 * means they must map to PyBytes_* on Python 3 (NOT PyUnicode_*).
 * ------------------------------------------------------------------
 */
#define PyString_FromStringAndSize   PyBytes_FromStringAndSize
#define PyString_FromString          PyBytes_FromString
#define PyString_AsString            PyBytes_AsString
#define PyString_AS_STRING           PyBytes_AS_STRING
#define PyString_GET_SIZE            PyBytes_GET_SIZE
#define PyString_Size                PyBytes_Size
#define PyString_Check               PyBytes_Check
#define _PyString_Resize             _PyBytes_Resize

/*
 * ------------------------------------------------------------------
 * Integer compatibility: PyInt_* -> PyLong_* on Python 3.
 * ------------------------------------------------------------------
 */
#define PyInt_FromLong               PyLong_FromLong
#define PyInt_AsLong                 PyLong_AsLong
#define PyInt_Check                  PyLong_Check
#define PyInt_FromSsize_t            PyLong_FromSsize_t
#define PyInt_AsSsize_t              PyLong_AsSsize_t

/*
 * Standard-library queue module name.
 */
#define _FIL_PY_QUEUE_MODULE_NAME    "queue"

/*
 * ------------------------------------------------------------------
 * Removed / deprecated C-API shims.
 * ------------------------------------------------------------------
 */

/* PyEval_InitThreads() is a no-op since 3.9 and was removed in 3.13. */
#if PY_VERSION_HEX >= 0x03090000
#ifdef PyEval_InitThreads
#undef PyEval_InitThreads
#endif
#define PyEval_InitThreads()         ((void)0)
#endif

/* PyImport_ImportModuleNoBlock was removed in 3.13; it has been a plain
 * alias for PyImport_ImportModule since 3.3. */
#if PY_VERSION_HEX >= 0x030d0000
#define PyImport_ImportModuleNoBlock PyImport_ImportModule
#endif

/* _Py_dup() is exported by libpython but its prototype is hidden behind
 * Py_BUILD_CORE in the public 3.x headers.  Forward-declare it (used by
 * the socket dup() implementation).  Signature: int _Py_dup(int). */
#ifndef Py_BUILD_CORE
extern int _Py_dup(int fd);
#endif

/*
 * ------------------------------------------------------------------
 * Module-init abstraction.
 *
 * On Python 3 a module is created via a PyModuleDef and PyInit_<name>()
 * returns the new module (or NULL on error).  _FIL_MODULE_SET() declares
 * a file-local static PyModuleDef (one _FIL_MODULE_SET per translation
 * unit) and assigns the created module to `mod`.
 * ------------------------------------------------------------------
 */
#define _FIL_MODULE_INIT_FN_NAME(name)   PyMODINIT_FUNC PyInit_##name(void)
#define _FIL_MODULE_INIT_ERROR           NULL
#define _FIL_MODULE_INIT_SUCCESS(mod)    (mod)
#define _FIL_MODULE_SET(mod, name, methods, doc)                          \
    static struct PyModuleDef _fil_module_def = {                         \
        PyModuleDef_HEAD_INIT,                                            \
        (name),   /* m_name */                                            \
        (doc),    /* m_doc */                                             \
        -1,       /* m_size */                                            \
        (methods),/* m_methods */                                         \
        NULL,     /* m_slots */                                           \
        NULL,     /* m_traverse */                                        \
        NULL,     /* m_clear */                                           \
        NULL      /* m_free */                                            \
    };                                                                    \
    (mod) = PyModule_Create(&_fil_module_def)

#else  /* Python 2 */

/*
 * On Python 2 PyString_*, PyInt_*, PyImport_ImportModuleNoBlock and
 * PyEval_InitThreads all exist natively -- no remapping required.
 */

#define _FIL_PY_QUEUE_MODULE_NAME    "Queue"

/*
 * On Python 2 module init is init<name>() returning void, and the module
 * is created with Py_InitModule3().
 */
#define _FIL_MODULE_INIT_FN_NAME(name)   PyMODINIT_FUNC init##name(void)
#define _FIL_MODULE_INIT_ERROR           /* return void */
#define _FIL_MODULE_INIT_SUCCESS(mod)    /* return void */
#define _FIL_MODULE_SET(mod, name, methods, doc)                          \
    (mod) = Py_InitModule3((name), (methods), (doc))

#endif  /* PY_MAJOR_VERSION >= 3 */

/*
 * ------------------------------------------------------------------
 * greenlet parent accessor compatibility.
 *
 * greenlet 3.x made PyGreenlet opaque; the parent is fetched with
 * PyGreenlet_GetParent() which returns a NEW reference.  Older greenlet
 * (the 1.1.x shipped for Py2.7) exposes the borrowed field via the
 * PyGreenlet_GET_PARENT() accessor; alias forward when the new-style
 * name is absent.  <greenlet.h> is included before this header, so the
 * modern macro is already visible when present.
 * ------------------------------------------------------------------
 */
#if !defined(PyGreenlet_GetParent)
/*
 * greenlet 1.1.x (Py2.7) has no PyGreenlet_GetParent(); the only accessor is
 * the PyGreenlet_GET_PARENT() macro, which reads the struct field directly and
 * therefore yields a *borrowed* reference.  The modern greenlet 3.x
 * PyGreenlet_GetParent() -- which all of filament's C code is written against --
 * returns a *new* reference (callers Py_XDECREF the result).  Bridge the two by
 * taking our own reference here so the Py2 path has identical ownership
 * semantics; otherwise the borrowed parent gets over-decref'd and freed early,
 * aborting with "greenlets cannot continue".
 */
static inline PyGreenlet *_fil_greenlet_get_parent(PyGreenlet *g)
{
    PyGreenlet *parent = PyGreenlet_GET_PARENT(g);
    Py_XINCREF(parent);
    return parent;
}
#define PyGreenlet_GetParent(g)      _fil_greenlet_get_parent((PyGreenlet *)(g))
#endif

#endif /* __FIL_CORE_PYVERSION_H__ */
