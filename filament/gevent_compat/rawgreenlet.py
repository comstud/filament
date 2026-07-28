# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.rawgreenlet
==================================

Drop-in replacement for the **top-level** ``greenlet`` package (injected as
``sys.modules['greenlet']`` by :func:`filament.gevent_compat.install`).

Why the shim has to own this name
---------------------------------
Under real gevent, ``gevent.Greenlet`` *is* a ``greenlet.greenlet`` subclass, so

    import greenlet
    greenlet.getcurrent() is <the gevent Greenlet currently running>

is an invariant that real code depends on.  The common shape is a scheduler
deciding whether the greenlet it is about to stop is the one it is running
on, and taking a completely different, self-kill-safe path when it is::

    if victim.greenlet is greenlet.getcurrent():
        victim.group.killone(victim.greenlet, block=False)

Under filament the invariant cannot hold by construction: our greenthreads run
on filament's private ``_fil_greenlet`` runtime, so the *installed* greenlet
package's ``getcurrent()`` reports the main greenlet no matter which
greenthread is running, and the compat :class:`~filament.gevent_compat.greenlet.Greenlet`
is a wrapper object rather than a greenlet subclass.  The comparison is then
permanently False and the self-kill path is never taken.

So we re-export the runtime filament actually switches on, and override
``getcurrent()`` to hand back the gevent-shaped ``Greenlet`` when the running
greenthread has one (see ``Greenlet._target``), falling back to the raw
greenthread otherwise -- which is what ``spawn_raw`` callers already hold.

As everywhere else in this package we only *register* a module under an
existing name; we never mutate the real greenlet package, so anything that
imported it before :func:`install` keeps the genuine article (filament's own
internals included), and :func:`uninstall` puts it back.
"""

from __future__ import absolute_import

try:
    # Python 3: filament's private vendored greenlet runtime.  This -- not the
    # installed greenlet package -- is the runtime every Filament lives on, so
    # its ``greenlet`` class is the correct base for isinstance() checks.
    import _fil_greenlet as _runtime
except ImportError:  # pragma: no cover - Python 2 / stock-greenlet build
    import greenlet as _runtime

# Re-export the runtime's whole public surface (greenlet, GreenletExit, error,
# settrace, the GREENLET_USE_* flags, ...) so this really is a drop-in; the
# explicit ``getcurrent`` below is defined afterwards and wins.
for _name in dir(_runtime):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_runtime, _name)
del _name

try:
    # The genuine package is still importable here: install() has not run yet
    # when this module is imported.  Some libraries version-gate on this.
    from greenlet import __version__
except ImportError:  # pragma: no cover
    __version__ = "3.0.0"


def getcurrent():
    """
    The currently running greenlet, gevent-style.

    Returns the compat ``Greenlet`` wrapper when the running greenthread is
    driving one, so ``greenlet.getcurrent()`` compares equal (by identity) to
    the object ``Group.spawn``/``gevent.spawn`` handed the caller -- matching
    gevent, where the two are literally the same object.  For a bare
    greenthread (``gevent.spawn_raw``, ``filament.spawn``) or the main
    greenlet there is no wrapper, and the greenthread itself is returned.
    """
    current = _runtime.getcurrent()
    wrapper = getattr(current, "_gevent_greenlet", None)
    return current if wrapper is None else wrapper
