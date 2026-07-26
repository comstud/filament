# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.tpool
==============

Offload *blocking* work to a pool of real OS threads while blocking only the
calling greenthread -- the filament-native equivalent of ``eventlet.tpool`` /
``gevent.threadpool``.

Why this exists (and why it matters)
------------------------------------
Cooperative greenthreads all share one OS thread; a genuinely blocking call (a
C library that doesn't yield, ``time.sleep`` from the stdlib, a slow syscall,
logging that grabs a threading.Lock, ...) would freeze *every* greenthread.
The C ``_filament.thrpool.ThreadPool`` runs such a callable on a separate OS
thread and does the "waiter dance": it parks the calling greenthread on the
scheduler and wakes it when the worker thread finishes, so the rest of the
program keeps running.

Crucially, the result is delivered back through the scheduler -- the worker
thread never switches greenlets itself.  That's what sidesteps the classic
cross-thread greenlet-switch corruption bug (cf. eventlet Bitbucket issue #137,
where logging from within a tpool-run callable could trigger a switch on the
wrong thread).  Route blocking calls through here and that whole class of bug
goes away.
"""

from __future__ import absolute_import

import atexit

from _filament.thrpool import ThreadPool


# Module-level default pool, created lazily on first use.
_default_pool = None


def _get_pool():
    global _default_pool
    if _default_pool is None:
        _default_pool = ThreadPool()
    return _default_pool


def _tpool_call(func, args, user_kwargs, kwargs=None, shutdown=None):
    # Runs on the OS worker thread.  The C ThreadPool.run() protocol passes its
    # own ``kwargs=`` and ``shutdown=`` keywords to whatever callable it runs,
    # so our target callable must accept them -- arbitrary user functions don't.
    # We therefore make _tpool_call the target and smuggle the real function,
    # its positional args, and its real keyword args through as *positionals*
    # (func, args, user_kwargs), leaving the protocol keywords for the pool.
    return func(*args, **user_kwargs)


def execute(func, *args, **kwargs):
    """
    Run ``func(*args, **kwargs)`` in a real OS thread from the default pool.

    Blocks ONLY the calling greenthread (other greenthreads keep running).
    Returns ``func``'s return value, or re-raises whatever it raised.
    """
    pool = _get_pool()
    return pool.run(_tpool_call, func, args, kwargs)


def set_num_threads(n):
    """
    Resize the default pool to ``n`` OS threads.

    Replaces the default pool (shutting the old one down); safe to call once at
    startup.  ``n`` becomes both the min and max thread count.
    """
    global _default_pool
    old = _default_pool
    _default_pool = ThreadPool(min_threads=n, max_threads=n)
    if old is not None:
        old.shutdown()


def shutdown():
    """Shut the default pool down (joining its worker threads)."""
    global _default_pool
    if _default_pool is not None:
        # wait=True: the default shutdown is asynchronous; callers (conftest,
        # atexit users) expect the workers to actually be gone on return --
        # workers still exiting can race interpreter teardown and abort.
        _default_pool.shutdown(wait=True)
        _default_pool = None


def _shutdown_at_exit():
    # Drop the default pool while the runtime is still fully alive.  The C
    # module registers its own atexit sweep as a backstop (it catches pools
    # nobody hands to us), but doing it here as well means the Python-visible
    # global is cleared too, so nothing can hand out a half-dead pool during
    # the rest of interpreter shutdown.  Registered at import time and after
    # _filament.thrpool's own hook, so atexit's LIFO order runs this first.
    try:
        if _default_pool is not None and not _default_pool.is_shutdown:
            shutdown()
    except Exception:
        pass


atexit.register(_shutdown_at_exit)


class Proxy(object):
    """
    Wrap ``obj`` so every method call is dispatched through :func:`execute`,
    i.e. runs on an OS worker thread without blocking the reactor.

    eventlet.tpool.Proxy parity:

    :param autowrap: a tuple of types; any return value that is an instance of
        one of them is itself wrapped in a Proxy (so chained calls stay off the
        cooperative thread).
    :param autowrap_names: attribute names whose return values are always
        wrapped in a Proxy, regardless of type.
    """

    # Use __slots__-free plain attributes but store them under names unlikely to
    # collide, and route everything else through __getattr__.  We stash config
    # on the instance __dict__ directly in __init__.
    def __init__(self, obj, autowrap=(), autowrap_names=()):
        # Bypass our own __setattr__ semantics by writing straight to __dict__.
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_autowrap", tuple(autowrap))
        object.__setattr__(self, "_autowrap_names", tuple(autowrap_names))

    def _wrap(self, value, name):
        # Decide whether a returned value/attribute should itself be proxied.
        if isinstance(value, self._autowrap) or name in self._autowrap_names:
            return Proxy(value, self._autowrap, self._autowrap_names)
        return value

    def __getattr__(self, name):
        attr = getattr(self._obj, name)
        if callable(attr):
            autowrap = self._autowrap
            autowrap_names = self._autowrap_names

            def caller(*args, **kwargs):
                result = execute(attr, *args, **kwargs)
                if isinstance(result, autowrap) or name in autowrap_names:
                    return Proxy(result, autowrap, autowrap_names)
                return result

            return caller
        # Non-callable attribute: return it directly (optionally proxied).
        return self._wrap(attr, name)

    def __setattr__(self, name, value):
        # Attribute writes go straight to the wrapped object.
        setattr(self._obj, name, value)

    def __call__(self, *args, **kwargs):
        # Proxying a callable object itself.
        return execute(self._obj, *args, **kwargs)

    # A few dunder methods aren't found via __getattr__, so forward the common
    # container ones explicitly through the thread pool.
    def __getitem__(self, key):
        return execute(self._obj.__getitem__, key)

    def __setitem__(self, key, value):
        return execute(self._obj.__setitem__, key, value)

    def __repr__(self):
        return execute(repr, self._obj)
