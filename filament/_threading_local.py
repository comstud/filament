"""Greenthread-local storage: a cooperative replacement for ``threading.local``.

The stdlib ``threading.local`` gives every OS thread its own independent set of
attributes on a single ``local`` instance.  Under filament, the unit of
concurrency is the *greenthread* (a greenlet), not the OS thread -- many
greenthreads share one OS thread -- so genuine parity requires per-greenthread
storage instead.  This module provides exactly that: a ``local`` whose
attributes are private to the currently-running filament.

Implementation notes
--------------------
* We key each instance's per-greenthread dict on the current greenlet object,
  held in a ``WeakKeyDictionary`` so that when a greenthread dies its storage is
  reclaimed automatically (no manual cleanup, no id-reuse hazard).
* Subclassing ``local`` and giving it an ``__init__``/class attributes works
  like the stdlib version: ``__init__`` runs once *per greenthread* the first
  time that greenthread touches the instance, with the original constructor
  args.
* Everything is single-threaded from the scheduler's point of view (greenthreads
  cooperate), so -- unlike the stdlib implementation -- we need no locking.
"""

import weakref

import filament as _fil


def _current_key():
    """Return the current greenthread object, used as the storage key."""
    return _fil.Filament.getcurrent()


class local(object):
    # We store all bookkeeping in private, name-mangled slots so subclasses can
    # freely use ordinary attribute names without colliding with us.
    __slots__ = ('_local__dicts', '_local__args', '_local__weakrefs',
                 '__dict__', '__weakref__')

    def __new__(cls, *args, **kwargs):
        if (args or kwargs) and cls.__init__ is object.__init__:
            # Match stdlib behaviour: passing args to a plain ``local()`` (one
            # that never defined __init__) is an error.
            raise TypeError('Initialization arguments are not supported')
        self = object.__new__(cls)
        # Map: greenthread -> that greenthread's private attribute dict.
        object.__setattr__(self, '_local__dicts', weakref.WeakKeyDictionary())
        # Remember the constructor args so we can re-run __init__ for each new
        # greenthread that first accesses this instance.
        object.__setattr__(self, '_local__args', (args, kwargs))
        return self

    def _local__get_dict(self):
        """Return (creating if needed) the current greenthread's attr dict.

        On first access from a given greenthread we install a fresh dict and,
        if the subclass defines ``__init__``, run it against this instance with
        the original constructor arguments -- exactly once for that
        greenthread.
        """
        dicts = object.__getattribute__(self, '_local__dicts')
        key = _current_key()
        d = dicts.get(key)
        if d is None:
            d = {}
            dicts[key] = d
            # Point the live __dict__ at this greenthread's storage before we
            # (possibly) call __init__, so __init__'s attribute writes land in
            # the right place.
            object.__setattr__(self, '__dict__', d)
            cls = type(self)
            if cls.__init__ is not object.__init__:
                args, kwargs = object.__getattribute__(self, '_local__args')
                cls.__init__(self, *args, **kwargs)
        return d

    def __getattribute__(self, name):
        # Swap in the current greenthread's dict, then defer to normal lookup.
        # (We must special-case our own private slots to avoid recursion.)
        if name in ('_local__get_dict', '_local__dicts', '_local__args'):
            return object.__getattribute__(self, name)
        d = object.__getattribute__(self, '_local__get_dict')()
        object.__setattr__(self, '__dict__', d)
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        if name == '__dict__':
            raise AttributeError(
                "%r object attribute '__dict__' is read-only"
                % type(self).__name__)
        d = object.__getattribute__(self, '_local__get_dict')()
        object.__setattr__(self, '__dict__', d)
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        if name == '__dict__':
            raise AttributeError(
                "%r object attribute '__dict__' is read-only"
                % type(self).__name__)
        d = object.__getattribute__(self, '_local__get_dict')()
        object.__setattr__(self, '__dict__', d)
        object.__delattr__(self, name)


# Marker so the patcher can install us in place of the stdlib module.
__filament__ = {'patch': '_threading_local'}
