import functools
import _socket
import types

from filament import thrpool

DEFAULT_MIN_THREADS = 5
DEFAULT_MAX_THREADS = 100
DEFAULT_STACK_SIZE = 128 * 1024

_proxy_methods = [
    'gethostbyname',
    'gethostbyname_ex',
    'gethostbyaddr',
    'getaddrinfo',
    'getnameinfo']

def _meth(name, self, *args, **kwargs):
    mkwargs = {'timeout': self.timeout}
    if kwargs:
        mkwargs[kwargs] = kwargs
    return self.run(
            getattr(_socket, name),
            *args,
            **mkwargs)

class Resolver(thrpool.ThreadPool):
    slots = ('timeout')

    def __new__(cls, *args, **kwargs):
        timeout = kwargs.pop('timeout', None)
        if timeout == 0.0:
            raise ValueError('timeout should be None or > 0')
        instance = super(Resolver, cls).__new__(cls, *args, **kwargs)
        instance.timeout = timeout
        return instance


for _methname in _proxy_methods:
    _p = functools.partial(_meth, _methname)
    _p.__name__ = _methname
    _p.__doc__ = getattr(_socket, _methname).__doc__
    setattr(Resolver, _methname, types.MethodType(_p, None, Resolver))
    del _p


del _proxy_methods
del _meth
del _methname

def get_resolver(*args, **kwargs):
    return Resolver(
            *args,
            min_threads=DEFAULT_MIN_THREADS,
            max_threads=DEFAULT_MAX_THREADS,
            stack_size=DEFAULT_STACK_SIZE,
            **kwargs)
