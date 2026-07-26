"""A DNS resolver that runs blocking name lookups in filament's thread pool.

The stdlib name-resolution calls (``getaddrinfo`` and friends) live in the C
``_socket`` module and block the calling OS thread.  To keep them from stalling
every greenthread, we run each lookup inside ``_filament.thrpool.ThreadPool``
(real OS worker threads) and cooperatively wait for the result.
"""

import atexit
import functools

import _socket

from filament import thrpool


DEFAULT_MIN_THREADS = 5
DEFAULT_MAX_THREADS = 100
DEFAULT_STACK_SIZE = 128 * 1024

# The blocking resolver functions we proxy into the thread pool.
_proxy_methods = [
    'gethostbyname',
    'gethostbyname_ex',
    'gethostbyaddr',
    'getaddrinfo',
    'getnameinfo',
]


def _meth(orig_meth, self, *args, **kwargs):
    """Run ``orig_meth(*args, **kwargs)`` in the pool and return its result.

    ``ThreadPool.run(fn, *args, kwargs=<dict>, timeout=<float>)``'s ``kwargs``
    is NOT expanded into keywords: run() calls ``fn(*args, kwargs=<dict>)``,
    handing the dict to ``fn`` as a literal ``kwargs`` keyword (see run()'s
    docstring).  The resolver functions (``getaddrinfo`` et al) obviously do
    not accept that, so bind the caller's keywords with ``partial`` instead and
    let run() call the result with positionals only.
    """
    if kwargs:
        orig_meth = functools.partial(orig_meth, **kwargs)
    return self.run(orig_meth, *args, timeout=self.timeout)


class Resolver(thrpool.ThreadPool):
    # NOTE: this was ``slots`` (a plain, ineffective class attribute) in the
    # original -- the real dunder is ``__slots__``.  ``('timeout',)`` must be a
    # tuple; the bare string ``'timeout'`` would (mis)declare seven 1-char slots.
    __slots__ = ('timeout',)

    def __new__(cls, *args, **kwargs):
        timeout = kwargs.pop('timeout', None)
        if timeout == 0.0:
            raise ValueError('timeout should be None or > 0')
        instance = super(Resolver, cls).__new__(cls, *args, **kwargs)
        instance.timeout = timeout
        return instance


def _make_proxy(orig_meth, name):
    """Build a bound-method-compatible proxy for ``orig_meth``.

    A plain function *is* a descriptor, so assigning it as a class attribute
    makes it bind ``self`` correctly on both Python 2 and 3.  (The original used
    ``functools.partial`` -- which is NOT a descriptor and so never bound
    ``self`` -- then tried to paper over it with ``types.MethodType`` bound to
    the *class*, which passed the class as ``self``.  This proxy avoids both
    bugs and needs no Py2/Py3 arity branch.)
    """
    def proxy(self, *args, **kwargs):
        return _meth(orig_meth, self, *args, **kwargs)
    proxy.__name__ = name
    try:
        proxy.__doc__ = getattr(orig_meth, '__doc__', None)
    except Exception:
        pass
    return proxy


for _methname in _proxy_methods:
    setattr(Resolver, _methname, _make_proxy(getattr(_socket, _methname), _methname))

# NOTE: we intentionally do NOT ``del _meth`` -- the proxies created above look
# it up as a module global at call time, so deleting it would break them.
del _methname
del _proxy_methods


def get_resolver(*args, **kwargs):
    """Create a Resolver backed by a pool with sensible default sizing.

    The resolver is shut down via ``atexit`` (while the runtime is still
    fully alive): ``_filament.socket`` builds its default resolver through
    here at import time and keeps it forever, and worker threads that
    outlive the interpreter try to re-attach their thread state during
    finalization -- a fatal error on 3.14+ (``gilstate_tss_set: failed to
    set current tstate``, seen intermittently at process exit).
    """
    kwargs.setdefault('min_threads', DEFAULT_MIN_THREADS)
    kwargs.setdefault('max_threads', DEFAULT_MAX_THREADS)
    kwargs.setdefault('stack_size', DEFAULT_STACK_SIZE)
    resolver = Resolver(*args, **kwargs)

    def _shutdown_at_exit():
        try:
            if not resolver.is_shutdown:
                # wait=True: the default shutdown is asynchronous (it even
                # spawns a coordinator thread); returning before the workers
                # have joined would leave them racing interpreter teardown.
                resolver.shutdown(wait=True)
        except Exception:
            pass

    atexit.register(_shutdown_at_exit)
    return resolver
