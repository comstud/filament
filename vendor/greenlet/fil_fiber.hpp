#ifndef FIL_FIBER_HPP
#define FIL_FIBER_HPP
/*
 * filament private-stack fiber core: low-level pieces.
 *
 *  - a minimal assembly context switch (save callee-saved registers on
 *    the current stack, store SP, load the target's SP, restore, ret);
 *  - per-fiber stacks: one anonymous mmap per fiber with a PROT_NONE
 *    guard page at the low end.  Pages are committed lazily by the
 *    kernel on first touch, so a generous virtual size costs nothing;
 *  - a stack pool (freelist) so the spawn path does not pay an
 *    mmap/mprotect/munmap round trip per greenthread.
 *
 * Only compiled when VGL_FIBER (see greenlet_cpython_compat.hpp).
 *
 * Concurrency: every call path into the pool (greenlet start, greenlet
 * finish, dealloc, thread teardown) runs while holding the GIL, but the
 * pool is shared across threads, so a plain pthread mutex guards the
 * freelist anyway; it is only touched at fiber birth/death, never on
 * the switch fast path.
 */

#include "greenlet_cpython_compat.hpp"

#if VGL_FIBER

#include <sys/mman.h>
#include <unistd.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>

extern "C" {
/* The switch primitive.  Saves the callee-saved register set + SP of
 * the calling context into *save_sp, installs restore_sp and returns
 * into whatever context that SP describes: either a context previously
 * saved by this same function (resuming its caller after our own
 * eventual suspension), or a seed context built by
 * fil_fiber_seed_context() (first activation: "returns" into
 * fil_fiber_entry on the fresh stack). */
void fil_fiber_asm_switch(void** save_sp, void* restore_sp);

/* First instructions of every new fiber; defined in greenlet.cpp
 * (needs the C++ greenlet types).  Reached via the seed context's
 * return address; never returns. */
void fil_fiber_entry(void);
}

#if defined(__aarch64__)
/*
 * AAPCS64 callee-saved set: x19-x28, x29 (fp), x30 (lr), and the low 64
 * bits of v8-v15 (d8-d15).  20 slots x 8 bytes = 160-byte context
 * frame, built with a single pre-indexed store so SP stays 16-aligned
 * at every instruction.  NEON high halves / SVE state are caller-saved
 * per the ABI, so a function call boundary (which this is) may clobber
 * them.  RET is exempt from BTI landing-pad requirements, so seeding a
 * context whose x30 targets fil_fiber_entry is BTI-safe.
 */
__asm__(
".text\n"
".align 4\n"
".globl fil_fiber_asm_switch\n"
".hidden fil_fiber_asm_switch\n"
".type fil_fiber_asm_switch, %function\n"
"fil_fiber_asm_switch:\n"
"    stp x29, x30, [sp, #-160]!\n"
"    stp x19, x20, [sp, #16]\n"
"    stp x21, x22, [sp, #32]\n"
"    stp x23, x24, [sp, #48]\n"
"    stp x25, x26, [sp, #64]\n"
"    stp x27, x28, [sp, #80]\n"
"    stp d8,  d9,  [sp, #96]\n"
"    stp d10, d11, [sp, #112]\n"
"    stp d12, d13, [sp, #128]\n"
"    stp d14, d15, [sp, #144]\n"
"    mov x2, sp\n"
"    str x2, [x0]\n"
"    mov sp, x1\n"
"    ldp x19, x20, [sp, #16]\n"
"    ldp x21, x22, [sp, #32]\n"
"    ldp x23, x24, [sp, #48]\n"
"    ldp x25, x26, [sp, #64]\n"
"    ldp x27, x28, [sp, #80]\n"
"    ldp d8,  d9,  [sp, #96]\n"
"    ldp d10, d11, [sp, #112]\n"
"    ldp d12, d13, [sp, #128]\n"
"    ldp d14, d15, [sp, #144]\n"
"    ldp x29, x30, [sp], #160\n"
"    ret\n"
".size fil_fiber_asm_switch, .-fil_fiber_asm_switch\n"
".previous\n"
);

namespace filfiber {
/* Build the initial ("seed") context on a fresh stack so that the
 * generic restore path of fil_fiber_asm_switch lands in
 * fil_fiber_entry.  Frame layout mirrors the asm above: x29 at +0
 * (zeroed: terminates fp-chain unwinds), x30 (the resume PC) at +8,
 * everything else zero.  Returns the seed SP. */
inline void* seed_context(char* stack_top) noexcept
{
    char* sp = stack_top - 160;
    memset(sp, 0, 160);
    reinterpret_cast<void**>(sp)[1] = reinterpret_cast<void*>(&fil_fiber_entry);
    return sp;
}
} // namespace filfiber

#elif defined(__x86_64__)
/*
 * System V AMD64 callee-saved set: rbx, rbp, r12-r15 (6 x 8 = 48-byte
 * context frame below the implicit return address pushed by our
 * caller's CALL).  mxcsr/x87cw are not saved, matching classic
 * greenlet's amd64 switch (the ABI declares the control bits
 * callee-preserved, but no CPython-adjacent code changes them).
 */
__asm__(
".text\n"
".align 16\n"
".globl fil_fiber_asm_switch\n"
".hidden fil_fiber_asm_switch\n"
".type fil_fiber_asm_switch, @function\n"
"fil_fiber_asm_switch:\n"
"    pushq %rbp\n"
"    pushq %rbx\n"
"    pushq %r12\n"
"    pushq %r13\n"
"    pushq %r14\n"
"    pushq %r15\n"
"    movq %rsp, (%rdi)\n"
"    movq %rsi, %rsp\n"
"    popq %r15\n"
"    popq %r14\n"
"    popq %r13\n"
"    popq %r12\n"
"    popq %rbx\n"
"    popq %rbp\n"
"    ret\n"
".size fil_fiber_asm_switch, .-fil_fiber_asm_switch\n"
".previous\n"
);

namespace filfiber {
/* Seed frame, matching the pop order above: [r15][r14][r13][r12][rbx]
 * [rbp][ret=fil_fiber_entry].  Seed SP is chosen so that after RET,
 * RSP % 16 == 8 at fil_fiber_entry's first instruction, exactly as if
 * it had been CALLed (System V alignment contract). */
inline void* seed_context(char* stack_top) noexcept
{
    char* top16 = reinterpret_cast<char*>(
        reinterpret_cast<uintptr_t>(stack_top) & ~static_cast<uintptr_t>(15));
    char* sp = top16 - 64; /* 8 spare + ret slot + 6 regs, sp % 16 == 0 */
    memset(sp, 0, 56);
    reinterpret_cast<void**>(sp)[6] = reinterpret_cast<void*>(&fil_fiber_entry);
    return sp;
}
} // namespace filfiber

#else
#  error "VGL_FIBER enabled on an unsupported architecture"
#endif

namespace filfiber {

/* Default 4 MiB of *virtual* stack per fiber (order of a few pages
 * actually committed in practice; Python-to-Python calls recurse on the
 * heap datastack on 3.11+, so the C stack only grows through C
 * extension re-entry).  Overridable via FIL_FIBER_STACK_SIZE (bytes,
 * min 64 KiB, rounded up to a page). */
static const size_t FIL_DEFAULT_STACK_SIZE = 4u * 1024u * 1024u;
/* Max stacks retained in the freelist; beyond this, death munmaps.
 * Sized to cover high-churn workloads with a few thousand concurrent
 * greenthreads (an echo server at concurrency 1000 keeps ~2000 fibers
 * live: client + server handler per connection): retaining a stack
 * costs only its virtual reservation plus however many pages the
 * previous tenant actually touched (typically a handful), while a pool
 * miss costs an mmap+mprotect on spawn and an munmap on death --
 * measured on the echo@1000 benchmark as several percent of throughput
 * and a >20% p99 latency penalty.  Worst-case retained VA at the
 * default 4 MiB stack is 16 GiB of *reservation* (not memory), reached
 * only if 4096 fibers were ever simultaneously live and then died.
 * Overridable via FIL_FIBER_POOL_MAX. */
static const size_t FIL_DEFAULT_POOL_MAX = 4096;

struct FreeStack {
    FreeStack* next;
};

static FreeStack* fil_pool_head = nullptr;
static size_t fil_pool_count = 0;
static size_t fil_pool_max = FIL_DEFAULT_POOL_MAX;
static size_t fil_page_size = 0;
static size_t fil_usable_size = 0;  /* stack bytes above the guard page */
static size_t fil_map_size = 0;     /* fil_usable_size + guard page */
static pthread_mutex_t fil_pool_lock = PTHREAD_MUTEX_INITIALIZER;

inline void init_sizes() noexcept
{
    if (fil_map_size) {
        return;
    }
    long ps = sysconf(_SC_PAGESIZE);
    fil_page_size = ps > 0 ? static_cast<size_t>(ps) : 4096;
    size_t sz = FIL_DEFAULT_STACK_SIZE;
    if (const char* e = getenv("FIL_FIBER_STACK_SIZE")) {
        if (*e) {
            char* end = nullptr;
            unsigned long long v = strtoull(e, &end, 0);
            if (end != e && v >= 64u * 1024u) {
                sz = static_cast<size_t>(v);
            }
        }
    }
    if (const char* e = getenv("FIL_FIBER_POOL_MAX")) {
        if (*e) {
            char* end = nullptr;
            unsigned long long v = strtoull(e, &end, 0);
            if (end != e) {
                fil_pool_max = static_cast<size_t>(v);
            }
        }
    }
    sz = (sz + fil_page_size - 1) & ~(fil_page_size - 1);
    fil_usable_size = sz;
    fil_map_size = sz + fil_page_size;
}

inline size_t usable_size() noexcept
{
    init_sizes();
    return fil_usable_size;
}

/* Returns the LOW usable address (just above the guard page), or null
 * (no Python error set; caller reports).  Stack top for seeding is
 * result + usable_size(). */
inline char* stack_alloc() noexcept
{
    init_sizes();
    pthread_mutex_lock(&fil_pool_lock);
    if (fil_pool_head) {
        FreeStack* f = fil_pool_head;
        fil_pool_head = f->next;
        fil_pool_count--;
        pthread_mutex_unlock(&fil_pool_lock);
        return reinterpret_cast<char*>(f);
    }
    pthread_mutex_unlock(&fil_pool_lock);
    void* m = mmap(nullptr, fil_map_size, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (m == MAP_FAILED) {
        return nullptr;
    }
    if (mprotect(m, fil_page_size, PROT_NONE) != 0) {
        munmap(m, fil_map_size);
        return nullptr;
    }
    return static_cast<char*>(m) + fil_page_size;
}

inline void stack_free(char* lo) noexcept
{
    pthread_mutex_lock(&fil_pool_lock);
    if (fil_pool_count < fil_pool_max) {
        FreeStack* f = reinterpret_cast<FreeStack*>(lo);
        f->next = fil_pool_head;
        fil_pool_head = f;
        fil_pool_count++;
        pthread_mutex_unlock(&fil_pool_lock);
        return;
    }
    pthread_mutex_unlock(&fil_pool_lock);
    munmap(lo - fil_page_size, fil_map_size);
}

} // namespace filfiber

#endif /* VGL_FIBER */
#endif /* FIL_FIBER_HPP */
