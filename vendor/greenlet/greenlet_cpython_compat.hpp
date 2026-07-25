/* -*- indent-tabs-mode: nil; tab-width: 4; -*- */
#ifndef GREENLET_CPYTHON_COMPAT_H
#define GREENLET_CPYTHON_COMPAT_H

/**
 * Helpers for compatibility with multiple versions of CPython.
 */

#define PY_SSIZE_T_CLEAN
#include "Python.h"

/*
Python 3.10 beta 1 changed tstate->use_tracing to a nested cframe member.
See https://github.com/python/cpython/pull/25276
We have to save and restore this as well.

Python 3.13 removed PyThreadState.cframe (GH-108035).
*/
#if PY_VERSION_HEX < 0x30D0000
#    define GREENLET_USE_CFRAME 1
#else
#    define GREENLET_USE_CFRAME 0
#endif


#if PY_VERSION_HEX >= 0x30B00A4
/*
Greenlet won't compile on anything older than Python 3.11 alpha 4 (see
https://bugs.python.org/issue46090). Summary of breaking internal changes:
- Python 3.11 alpha 1 changed how frame objects are represented internally.
  - https://github.com/python/cpython/pull/30122
- Python 3.11 alpha 3 changed how recursion limits are stored.
  - https://github.com/python/cpython/pull/29524
- Python 3.11 alpha 4 changed how exception state is stored. It also includes a
  change to help greenlet save and restore the interpreter frame "data stack".
  - https://github.com/python/cpython/pull/30122
  - https://github.com/python/cpython/pull/30234
*/
#    define GREENLET_PY311 1
#else
#    define GREENLET_PY311 0
#endif


#if PY_VERSION_HEX >= 0x30C0000
#    define GREENLET_PY312 1
#else
#    define GREENLET_PY312 0
#endif

#if PY_VERSION_HEX >= 0x30D0000
#    define GREENLET_PY313 1
#else
#    define GREENLET_PY313 0
#endif

#if PY_VERSION_HEX >= 0x30E0000
#    define GREENLET_PY314 1
#else
#    define GREENLET_PY314 0
#endif

#if PY_VERSION_HEX >= 0x30F0000
#    define GREENLET_PY315 1
#else
#    define GREENLET_PY315 0
#endif


/* _Py_DEC_REFTOTAL macro has been removed from Python 3.9 by:
  https://github.com/python/cpython/commit/49932fec62c616ec88da52642339d83ae719e924

  The symbol we use to replace it was removed by at least 3.12.
*/
#if defined(Py_REF_DEBUG) && !GREENLET_PY312
#    define GREENLET_Py_DEC_REFTOTAL _Py_RefTotal--
#else
#    define GREENLET_Py_DEC_REFTOTAL
#endif


#define G_TPFLAGS_DEFAULT Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_VERSION_TAG | Py_TPFLAGS_HAVE_GC


// bpo-43760 added PyThreadState_EnterTracing() to Python 3.11.0a2
#if PY_VERSION_HEX < 0x030B00A2 && !defined(PYPY_VERSION)
static inline void PyThreadState_EnterTracing(PyThreadState *tstate)
{
    tstate->tracing++;
    tstate->cframe->use_tracing = 0;
}
#endif

// bpo-43760 added PyThreadState_LeaveTracing() to Python 3.11.0a2
#if PY_VERSION_HEX < 0x030B00A2 && !defined(PYPY_VERSION)
static inline void PyThreadState_LeaveTracing(PyThreadState *tstate)
{
    tstate->tracing--;
    int use_tracing = (tstate->c_tracefunc != NULL
                       || tstate->c_profilefunc != NULL);
    tstate->cframe->use_tracing = use_tracing;
}
#endif

#if !defined(Py_C_RECURSION_LIMIT) && defined(C_RECURSION_LIMIT)
#  define Py_C_RECURSION_LIMIT C_RECURSION_LIMIT
#endif

// Py_IsFinalizing() became a public API in Python 3.13. Map it to the
// private _Py_IsFinalizing() on older versions so all call sites can
// use the standard name. Remove this once greenlet drops support for
// Python < 3.13.
#if !GREENLET_PY313
#  define Py_IsFinalizing() _Py_IsFinalizing()
#endif

/* filament: runtime-selectable lazy frame materialization.
 *
 * When enabled, the per-switch introspection work (eager top-frame
 * materialization via PyThreadState_GetFrame plus the 3.12+
 * expose_frames() walk of the interpreter frame chain) is skipped
 * unless debug mode is armed (vgl_debug_mode global, or a per-thread
 * trace/profile function).  A parked greenlet's frames are then
 * reconstructed on demand from the saved PythonState::current_frame
 * (see Greenlet::vgl_materialize_frames).
 *
 * Version gating -- deliberate, not an oversight:
 *  - < 3.11: saving tstate->frame is mandatory state management (the
 *    restore path needs it), and it is a free pointer steal.  Always
 *    eager; the runtime flag has no effect.
 *  - 3.11: no expose/unexpose machinery exists and we have no 3.11
 *    interpreter in the test matrix; stays eager.
 *  - 3.12/3.13 (GIL builds): lazy path implemented and tested.
 *  - 3.14+: operator<< must capture this->stackpointer *through* the
 *    materialized top frame at switch-out for a correct restore, so
 *    materialization genuinely cannot be deferred there.  Stays eager.
 *  - free-threaded builds: the C-stack-ref snapshot / stackref GC
 *    interactions have not been validated with lazy frames.  Eager.
 */
#if GREENLET_PY312 && !GREENLET_PY314 && !defined(Py_GIL_DISABLED) \
    && !defined(VGL_NO_RUNTIME_LAZY)
#  define VGL_RUNTIME_LAZY 1
#else
#  define VGL_RUNTIME_LAZY 0
#endif

/* filament: private-stack fiber core (build-time opt-in).
 *
 * When the extension is built with -DFIL_FIBER_CORE (setup.py sets this
 * when the FIL_FIBER_CORE environment variable is truthy at build time),
 * greenlets run on their own mmap'd stacks (guard page + lazily
 * committed pages, pooled across spawns) and switching is a minimal
 * save-callee-saved-registers / swap-SP assembly primitive instead of
 * the classic stack-slicing memcpy dance.  The PyThreadState bookkeeping
 * (TPythonState / TExceptionState) is unchanged; only StackState and the
 * low-level switch differ.
 *
 * Version/platform gating:
 *  - 3.10b1 .. 3.15: every version whose PyThreadState save/restore is
 *    already covered by TPythonState.cpp.  On the tstate->cframe era
 *    (3.10..3.12, GREENLET_USE_CFRAME) the classic core parks the
 *    child's initial _PyCFrame in g_initialstub's stack frame -- correct
 *    only under stack borrowing, where that frame is part of the child's
 *    sliced stack.  The fiber core instead reserves the _PyCFrame slot
 *    at the top of the child's OWN private stack (StackState::
 *    fil_init_fiber), which persists for the fiber's whole lifetime, so
 *    the relocation is what makes <=3.12 eligible.
 *  - On 3.14 the two version-specific pieces are core-independent and
 *    run identically under the fiber core:
 *      - tstate->current_executor (JIT) is saved/restored by
 *        PythonState::operator<< / operator>> exactly as in the classic
 *        core (g_switchstack performs both around the asm switch);
 *      - the stackpointer capture through the materialized top frame at
 *        switch-out only reads _top_frame->f_frame, which lives in the
 *        heap datastack chunks -- and with private stacks nothing is
 *        sliced anyway (_stack_saved stays 0, copy_from_stack is plain
 *        memcpy), so the frame addresses operator<< sees are the true
 *        ones.  3.14's machine-SP recursion/trashcan bounds are remapped
 *        onto the private stack (fil_set_stack_limits).
 *  - 3.15: same layout as 3.14 for everything the fiber core touches
 *    (c_stack_{top,soft_limit,hard_limit} bounds swap included); the
 *    3.15-only changes (stackref-walking tp_traverse/tp_clear,
 *    FRAME_OWNED_BY_CSTACK removal, tp_is_gc removal) are
 *    core-independent.  Validated against 3.15.0b4.
 *  - GIL builds only: the free-threaded C-stack-ref machinery snapshots
 *    assume the classic core.
 *  - aarch64 + x86_64 System V only (the two asm switch flavors below).
 */
/* setup.py materializes the FIL_FIBER_CORE build flag as a generated
 * header (setuptools does not forward CFLAGS to C++ compiles). */
#if defined(__has_include)
#  if __has_include("fil_fiber_config.h")
#    include "fil_fiber_config.h"
#  endif
#endif
#if defined(FIL_FIBER_CORE) && PY_VERSION_HEX >= 0x30A00B1 \
    && !defined(Py_GIL_DISABLED) \
    && (defined(__aarch64__) || (defined(__x86_64__) && !defined(_WIN64)))
#  define VGL_FIBER 1
#else
#  define VGL_FIBER 0
#endif

#endif /* GREENLET_CPYTHON_COMPAT_H */
