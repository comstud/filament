#ifndef GREENLET_STACK_STATE_CPP
#define GREENLET_STACK_STATE_CPP

#include "TGreenlet.hpp"
extern "C" { extern unsigned long long vgl_bytes_saved, vgl_bytes_restored; }

namespace greenlet {

#ifdef GREENLET_USE_STDIO
#include <iostream>
using std::cerr;
using std::endl;

std::ostream& operator<<(std::ostream& os, const StackState& s)
{
    os << "StackState(stack_start=" << (void*)s._stack_start
       << ", stack_stop=" << (void*)s.stack_stop
       << ", stack_copy=" << (void*)s.stack_copy
       << ", stack_saved=" << s._stack_saved
       << ", stack_prev=" << s.stack_prev
       << ", addr=" << &s
       << ")";
    return os;
}
#endif

StackState::StackState(void* mark, StackState& current)
    : _stack_start(nullptr),
      stack_stop((char*)mark),
      stack_copy(nullptr),
      _stack_saved(0),
      _stack_capacity(0),
      stack_prev(current._stack_start
                 ? &current
                 : current.stack_prev)
#if VGL_FIBER
      ,fil_sp(nullptr)
      ,fil_stack_lo(nullptr)
#if GREENLET_USE_CFRAME
      ,fil_cframe(nullptr)
#endif
#endif
{
    /* Skip a dying greenlet (see stack_prev initializer) */
}

StackState::StackState()
    : _stack_start(nullptr),
      stack_stop(nullptr),
      stack_copy(nullptr),
      _stack_saved(0),
      _stack_capacity(0),
      stack_prev(nullptr)
#if VGL_FIBER
      ,fil_sp(nullptr)
      ,fil_stack_lo(nullptr)
#if GREENLET_USE_CFRAME
      ,fil_cframe(nullptr)
#endif
#endif
{
}

StackState::StackState(const StackState& other)
// can't use a delegating constructor because of
// MSVC for Python 2.7
    : _stack_start(nullptr),
      stack_stop(nullptr),
      stack_copy(nullptr),
      _stack_saved(0),
      _stack_capacity(0),
      stack_prev(nullptr)
#if VGL_FIBER
      ,fil_sp(nullptr)
      ,fil_stack_lo(nullptr)
#if GREENLET_USE_CFRAME
      ,fil_cframe(nullptr)
#endif
#endif
{
    this->operator=(other);
}

StackState& StackState::operator=(const StackState& other)
{
    if (&other == this) {
        return *this;
    }
    if (other._stack_saved) {
        throw std::runtime_error("Refusing to steal memory.");
    }
#if VGL_FIBER
    if (other.fil_stack_lo) {
        // Copying ownership of a live fiber stack is never valid; the
        // only assignments performed on fiber states carry empty/main
        // right-hand sides (deactivate_and_free, murder_in_place,
        // failed-start cleanup).
        throw std::runtime_error("Refusing to steal a fiber stack.");
    }
#endif

    //If we have memory allocated, dispose of it
    this->free_stack_copy();
#if VGL_FIBER
    // Ditto for our private stack: assignment only ever happens when
    // this greenlet cannot run again (it is being reset to the
    // unstarted/dead state, or destroyed), so the stack is recyclable.
    this->fil_release_stack();
    this->fil_sp = other.fil_sp;  // null for default/main states
#if GREENLET_USE_CFRAME
    this->fil_cframe = other.fil_cframe;  // ditto
#endif
#endif

    this->_stack_start = other._stack_start;
    this->stack_stop = other.stack_stop;
    // Don't alias *other*'s retained (but logically empty) copy
    // buffer; each StackState exclusively owns its own buffer. We
    // know it's logically empty because of the check above.
    this->stack_copy = nullptr;
    this->_stack_saved = 0;
    this->_stack_capacity = 0;
    this->stack_prev = other.stack_prev;
    return *this;
}

#if VGL_FIBER
bool StackState::fil_init_fiber()
{
    assert(!this->fil_stack_lo);
    assert(!this->stack_stop); // not started
    char* lo = filfiber::stack_alloc();
    if (!lo) {
        PyErr_NoMemory();
        return false;
    }
    char* top = lo + filfiber::usable_size();
    this->fil_stack_lo = lo;
#if GREENLET_USE_CFRAME
    /* 3.10..3.12: reserve the fiber's initial _PyCFrame at the very top
     * of its private stack, above the seed context, so it lives exactly
     * as long as the fiber can run (see the member comment in
     * TGreenlet.hpp).  16-byte alignment keeps the seed SP contract of
     * both asm flavors intact. */
    {
        size_t cfsz = (sizeof(_PyCFrame) + 15u) & ~static_cast<size_t>(15u);
        top -= cfsz;
        this->fil_cframe = reinterpret_cast<_PyCFrame*>(top);
    }
#endif
    this->fil_sp = filfiber::seed_context(top);
    // Marks started() (and can never collide with the main() sentinel).
    this->stack_stop = top;
    // Not active() until inner_bootstrap's set_active() on first run.
    this->_stack_start = nullptr;
    this->stack_prev = nullptr;
    return true;
}

void StackState::fil_release_stack() noexcept
{
    if (this->fil_stack_lo) {
        filfiber::stack_free(this->fil_stack_lo);
        this->fil_stack_lo = nullptr;
        this->fil_sp = nullptr;
#if GREENLET_USE_CFRAME
        this->fil_cframe = nullptr;
#endif
    }
}
#endif

inline void StackState::free_stack_copy() noexcept
{
    PyMem_Free(this->stack_copy);
    this->stack_copy = nullptr;
    this->_stack_saved = 0;
    this->_stack_capacity = 0;
}

inline void StackState::copy_heap_to_stack(const StackState& current) noexcept
{

    /* Restore the heap copy back into the C stack */
    if (this->_stack_saved != 0) {
        { vgl_bytes_restored += this->_stack_saved; }
        memcpy(this->_stack_start, this->stack_copy, this->_stack_saved);
        // Keep the buffer (at its high-water capacity) for the next
        // time we're suspended; only the logical size resets. The
        // buffer is finally released when the greenlet finishes
        // (``set_inactive``) or is destroyed.
        this->_stack_saved = 0;
    }
    StackState* owner = const_cast<StackState*>(&current);
    if (!owner->_stack_start) {
        owner = owner->stack_prev; /* greenlet is dying, skip it */
    }
    while (owner && owner->stack_stop <= this->stack_stop) {
        // cerr << "\tOwner: " << owner << endl;
        owner = owner->stack_prev; /* find greenlet with more stack */
    }
    this->stack_prev = owner;
    // cerr << "\tFinished with: " << *this << endl;
}

inline int StackState::copy_stack_to_heap_up_to(const char* const stop) noexcept
{
    /* Save more of g's stack into the heap -- at least up to 'stop'
       g->stack_stop |________|
                     |        |
                     |    __ stop       . . . . .
                     |        |    ==>  .       .
                     |________|          _______
                     |        |         |       |
                     |        |         |       |
      g->stack_start |        |         |_______| g->stack_copy
     */
    intptr_t sz1 = this->_stack_saved;
    intptr_t sz2 = stop - this->_stack_start;
    assert(this->_stack_start);
    if (sz2 > sz1) {
        if (sz2 > this->_stack_capacity) {
            // Grow the buffer. It is retained at its high-water
            // capacity across switches, so on steady-state switch
            // paths this branch isn't taken and no allocator call is
            // made.
            char* c = (char*)PyMem_Realloc(this->stack_copy, sz2);
            if (!c) {
                PyErr_NoMemory();
                return -1;
            }
            this->stack_copy = c;
            this->_stack_capacity = sz2;
        }
        { vgl_bytes_saved += sz2 - sz1; }
        memcpy(this->stack_copy + sz1, this->_stack_start + sz1, sz2 - sz1);
        this->_stack_saved = sz2;
    }
    return 0;
}

inline int StackState::copy_stack_to_heap(char* const stackref,
                                          const StackState& current) noexcept
{
    /* must free all the C stack up to target_stop */
    const char* const target_stop = this->stack_stop;

    StackState* owner = const_cast<StackState*>(&current);
    assert(owner->_stack_saved == 0); // everything is present on the stack
    if (!owner->_stack_start) {
        owner = owner->stack_prev; /* not saved if dying */
    }
    else {
        owner->_stack_start = stackref;
    }

    while (owner->stack_stop < target_stop) {
        /* ts_current is entierely within the area to free */
        if (owner->copy_stack_to_heap_up_to(owner->stack_stop)) {
            return -1; /* XXX */
        }
        owner = owner->stack_prev;
    }
    if (owner != this) {
        if (owner->copy_stack_to_heap_up_to(target_stop)) {
            return -1; /* XXX */
        }
    }
    return 0;
}

inline bool StackState::started() const noexcept
{
    return this->stack_stop != nullptr;
}

inline bool StackState::main() const noexcept
{
    return this->stack_stop == (char*)-1;
}

inline bool StackState::active() const noexcept
{
    return this->_stack_start != nullptr;
}

inline void StackState::set_active() noexcept
{
    assert(this->_stack_start == nullptr);
    this->_stack_start = (char*)1;
}

inline void StackState::set_inactive() noexcept
{
    this->_stack_start = nullptr;
    // XXX: What if we still have memory out there?
    // That case is actually triggered by
    // test_issue251_issue252_explicit_reference_not_collectable (greenlet.tests.test_leaks.TestLeaks)
    // and
    // test_issue251_issue252_need_to_collect_in_background
    // (greenlet.tests.test_leaks.TestLeaks)
    //
    // Those objects never get deallocated, so the destructor never
    // runs.
    // It *seems* safe to clean up the memory here?
    //
    // Note that we check *stack_copy*, not *_stack_saved*: the
    // buffer is retained (with ``_stack_saved == 0``) while the
    // greenlet runs, and this is where it's finally released.
    if (this->stack_copy) {
        this->free_stack_copy();
    }
}

inline intptr_t StackState::stack_saved() const noexcept
{
    return this->_stack_saved;
}

inline char* StackState::stack_start() const noexcept
{
    return this->_stack_start;
}


inline StackState StackState::make_main() noexcept
{
    StackState s;
    s._stack_start = (char*)1;
    s.stack_stop = (char*)-1;
    return s;
}

StackState::~StackState()
{
    if (this->stack_copy) {
        this->free_stack_copy();
    }
#if VGL_FIBER
    // By the time a Greenlet is destroyed its fiber (if any) can no
    // longer run: green_dealloc kills active non-main greenlets first,
    // and finished fibers already released their stack on the switch
    // away.  This catches murdered/abandoned ones.
    this->fil_release_stack();
#endif
}

void StackState::copy_from_stack(void* vdest, const void* vsrc, size_t n) const
{
    char* dest = static_cast<char*>(vdest);
    const char* src = static_cast<const char*>(vsrc);
    if (src + n <= this->_stack_start
        || src >= this->_stack_start + this->_stack_saved
        || this->_stack_saved == 0) {
        // Nothing we're copying was spilled from the stack
        memcpy(dest, src, n);
        return;
    }

    if (src < this->_stack_start) {
        // Copy the part before the saved stack.
        // We know src + n > _stack_start due to the test above.
        const size_t nbefore = this->_stack_start - src;
        memcpy(dest, src, nbefore);
        dest += nbefore;
        src += nbefore;
        n -= nbefore;
    }
    // We know src >= _stack_start after the before-copy, and
    // src < _stack_start + _stack_saved due to the first if condition
    size_t nspilled = std::min<size_t>(n, this->_stack_start + this->_stack_saved - src);
    memcpy(dest, this->stack_copy + (src - this->_stack_start), nspilled);
    dest += nspilled;
    src += nspilled;
    n -= nspilled;
    if (n > 0) {
        // Copy the part after the saved stack
        memcpy(dest, src, n);
    }
}

}; // namespace greenlet

#endif // GREENLET_STACK_STATE_CPP
