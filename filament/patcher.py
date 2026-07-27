"""Filament monkey-patching engine.

This is filament's equivalent of ``gevent.monkey`` / ``eventlet.monkey_patch``.
It swaps standard-library modules (or individual items inside them) for
filament's cooperative, greenthread-aware replacements so that ordinary,
un-modified Python code yields to the filament scheduler instead of blocking
the whole process.

Two patching mechanisms are supported:

1. **Whole-module replacement.**  A green module declares a marker
   ``__filament__ = {'patch': '<stdlib name>'}`` and we install it into
   ``sys.modules`` under that stdlib name (this is how socket/ssl/time/etc.
   are greened).

2. **Item-level replacement.**  A green module declares
   ``__filament__ = {'patch': '<stdlib name>', 'items': [...]}``; instead of
   replacing the whole module we ``setattr`` the named items onto the real
   stdlib module (this is how ``os.read``/``os.write`` are greened -- we do
   NOT want to replace all of ``os``).

Everything here is written to run on Python 2.7 and Python 3.5 - 3.13.  In
particular we avoid f-strings and are careful about the several stdlib names
that were renamed between Python 2 and 3 (``thread``->``_thread``,
``Queue``->``queue``, ``collections.Iterable``->``collections.abc.Iterable``).
"""

import gc
import sys

from filament import _util


# ---------------------------------------------------------------------------
# Python 2 / 3 compatibility shims
# ---------------------------------------------------------------------------

# ``collections.Iterable`` moved to ``collections.abc.Iterable`` in Python 3.3
# and the old alias was *removed* in Python 3.10.  Importing it the old way is
# exactly what used to make this module explode on import under modern Python
# (the original bug at patcher.py:30).  Import it in a version-safe way.
try:
    from collections.abc import Iterable as _Iterable
except ImportError:  # pragma: no cover - Python 2.7 / < 3.3
    from collections import Iterable as _Iterable

_PY3 = sys.version_info[0] >= 3

# Map the "logical" name we use in filament land to the actual stdlib module
# name for the running interpreter.  On Python 3 the low-level thread module is
# ``_thread`` and the queue module is ``queue``; on Python 2 they are ``thread``
# and ``Queue`` respectively.
_THREAD_MODULE = '_thread' if _PY3 else 'thread'
_QUEUE_MODULE = 'queue' if _PY3 else 'Queue'


# ---------------------------------------------------------------------------
# State: originals + introspection
# ---------------------------------------------------------------------------

# ``_originals`` maps a *module name* to the original (pristine, un-greened)
# module object, captured at the moment we first patch it.  Used by
# ``get_original`` and to keep a live reference so the module's globals dict is
# not torn down while third-party code may still be holding it.
_originals = {}

# ``saved`` maps a module name to a dict of {item_name: original_value} for
# item-level patches (and, for whole-module swaps, a single sentinel key so
# that ``is_module_patched`` / ``get_original`` have something to key on).
# This mirrors gevent's ``gevent.monkey.saved`` and is part of our public
# introspection surface.
saved = {}

# Set of module names that have been fully replaced in ``sys.modules``.
_patched_modules = set()

# Sentinel key used inside ``saved[name]`` to stash a whole-module original.
_MODULE_KEY = '__module__'


def _import(name):
    """Import ``name`` and return the resulting module object.

    ``__import__`` returns the top-level package for dotted names, so we fetch
    the real (possibly sub-) module out of ``sys.modules`` afterwards.
    """
    __import__(name)
    return sys.modules[name]


# ---------------------------------------------------------------------------
# Introspection helpers (public API, for parity with gevent/eventlet)
# ---------------------------------------------------------------------------

def is_module_patched(module_name):
    """Return True if the named stdlib module has been greened."""
    if module_name in _patched_modules:
        return True
    # Also treat a module that is currently carrying a filament marker as
    # patched (covers the case where something imported the green module and
    # installed it without going through us).
    mod = sys.modules.get(module_name)
    return bool(mod is not None and getattr(mod, '__filament__', None))


def is_object_patched(module_name, item_name):
    """Return True if ``module_name.item_name`` has been individually greened."""
    items = saved.get(module_name)
    return bool(items and item_name in items and item_name != _MODULE_KEY)


def get_original(module_name, item_name=None):
    """Return the original, un-greened module or attribute.

    ``get_original('socket')`` -> the real ``socket`` module.
    ``get_original('socket', 'socket')`` -> the real ``socket.socket`` class.
    ``get_original('os', ['read', 'write'])`` -> a list of the originals.

    We consult our saved originals first (captured at patch time); if the
    thing was never patched we simply import/attribute it live -- in that case
    the live value *is* the original.
    """
    orig_mod = _originals.get(module_name)
    if orig_mod is None:
        # Never patched at the whole-module level.  The value currently in
        # sys.modules is therefore still the original.
        orig_mod = _import(module_name)

    if item_name is None:
        return orig_mod

    def _one(name):
        items = saved.get(module_name)
        if items and name in items and name != _MODULE_KEY:
            return items[name]
        return getattr(orig_mod, name)

    if isinstance(item_name, (list, tuple)):
        return [_one(n) for n in item_name]
    return _one(item_name)


# ---------------------------------------------------------------------------
# Low-level patch primitives
# ---------------------------------------------------------------------------

def _record_module_original(name, orig):
    """Remember the original module object exactly once."""
    if name not in _originals:
        _originals[name] = orig
        saved.setdefault(name, {})[_MODULE_KEY] = orig
        # Keep the original module (and its globals dict) alive so that code
        # still referencing it does not see its globals blown away to None.
        _util._hold_refs(orig)


def _patch_module(stdlib_name, green_mod):
    """Fully replace ``sys.modules[stdlib_name]`` with ``green_mod``.

    Idempotent: patching an already-patched module is a no-op.
    """
    if is_module_patched(stdlib_name):
        return
    orig = sys.modules.get(stdlib_name)
    if orig is None:
        try:
            orig = _import(stdlib_name)
        except ImportError:
            orig = None
    _record_module_original(stdlib_name, orig)
    sys.modules[stdlib_name] = green_mod
    _patched_modules.add(stdlib_name)


def patch_item(module, item_name, green_value):
    """Replace ``module.item_name`` with ``green_value``, saving the original.

    ``module`` may be a module object or a module name.  Idempotent per item.
    """
    if _util._is_str(module):
        module = _import(module)
    module_name = module.__name__
    items = saved.setdefault(module_name, {})
    if item_name in items:
        # Already patched this item; keep it idempotent.
        return
    original = getattr(module, item_name, None)
    items[item_name] = original
    setattr(module, item_name, green_value)


def _patch_green(fil_submodule):
    """Import ``filament.<fil_submodule>`` and apply it per its marker.

    Reads the module's ``__filament__`` marker to decide whether to do a
    whole-module swap (default) or item-level patching (when an ``items`` key
    is present).  Returns the green module (useful to callers that then want to
    do extra work, e.g. patch_thread).
    """
    green = _import('filament.' + fil_submodule)
    marker = getattr(green, '__filament__', {})
    target = marker.get('patch', fil_submodule)
    items = marker.get('items')
    if items:
        # Item-level: green must expose each named attribute.
        real = _import(target)
        for item_name in items:
            patch_item(real, item_name, getattr(green, item_name))
    else:
        _patch_module(target, green)
    return green


# ---------------------------------------------------------------------------
# Granular public patch functions
# ---------------------------------------------------------------------------

def patch_socket(dns=True, aggressive=True):
    """Green the ``socket`` module.

    The green socket already resolves hostnames cooperatively (its DNS calls go
    through filament's thread-pool resolver), so ``dns=True`` here is mostly for
    API parity with gevent/eventlet -- when True we additionally make sure the
    resolver functions are greened even if only pieces of socket are wanted.
    """
    _patch_green('socket')
    if dns:
        patch_dns()


def patch_dns():
    """Green just the DNS-resolution functions on the ``socket`` module.

    filament resolves names in its C thread pool (see ``_filament.socket`` /
    ``filament.thrpool_resolver``).  We copy those cooperative resolver
    functions over the stdlib socket module's blocking ones.  This works
    whether or not the whole socket module has been swapped.
    """
    try:
        from _filament import socket as _fil_socket
    except ImportError:  # pragma: no cover - C ext not built
        return
    # Prefer filament.socket's versions over the raw C ones: it is a copy of
    # the stdlib module built over the cooperative _socket, so its
    # getaddrinfo() is the stdlib wrapper -- cooperative *and* returning
    # AddressFamily/SocketKind enums rather than bare ints.  Real code reads
    # those back (IPv6 support is commonly detected by looking for
    # "Family.AF_INET6" in the repr), so the conversion is not cosmetic.
    try:
        from filament import socket as _green_socket
    except ImportError:  # pragma: no cover - partial install
        _green_socket = None
    socket_mod = sys.modules.get('socket') or _import('socket')
    for name in ('getaddrinfo', 'gethostbyname', 'gethostbyname_ex',
                 'gethostbyaddr', 'getnameinfo'):
        green = getattr(_green_socket, name, None) \
            or getattr(_fil_socket, name, None)
        if green is not None and hasattr(socket_mod, name):
            patch_item(socket_mod, name, green)


def patch_ssl():
    """Green the ``ssl`` module so TLS sockets cooperate with filaments."""
    _patch_green('ssl')


def patch_time():
    """Green the ``time`` module (``time.sleep`` -> ``filament.sleep``)."""
    _patch_green('time')


def patch_select():
    """Green the ``select`` module (cooperative ``select.select``)."""
    _patch_green('select')


def patch_os():
    """Green cooperative bits of ``os`` (read/write/fdopen), item-level.

    We deliberately do NOT replace the whole ``os`` module -- it contains
    hundreds of names, most of which we must leave alone.  ``filament.os``
    declares an ``items`` marker so ``_patch_green`` does item-level setattr.
    """
    _patch_green('os')


def patch_queue():
    """Green the ``queue`` (Py3) / ``Queue`` (Py2) module."""
    _patch_green('queue')


def patch_subprocess():
    """Green ``subprocess`` so ``Popen.wait``/``communicate`` cooperate."""
    try:
        _patch_green('subprocess')
    except ImportError:  # pragma: no cover - optional module
        # subprocess greening is best-effort; if the green module is not
        # importable (e.g. a sibling primitive it needs is missing) we just
        # skip rather than break patch_all().
        pass


def patch_thread(threading=True, _threading_local=True, Event=True,
                 logging=True, existing_locks=True):
    """Green the low-level ``thread``/``_thread`` module and friends.

    Parameters mirror gevent's ``patch_thread``:

    * ``threading`` -- also green the high-level ``threading`` module.
    * ``_threading_local`` -- green ``threading.local`` / ``_threading_local``
      to be *greenthread*-local rather than OS-thread-local.
    * ``Event`` -- green ``threading.Event``.
    * ``logging`` / ``existing_locks`` -- see :func:`_patch_existing_locks`.
      These convert locks that were *already created* before patching to
      cooperative locks; doing so is what prevents the historical
      cross-greenlet logging deadlock (see that function for the full write-up
      and the eventlet issue #137 citation).
    """
    # Green the low-level thread module first (_thread on Py3, thread on Py2).
    green_thread = _import('filament.thread')
    _patch_module(_THREAD_MODULE, green_thread)

    if threading:
        _patch_green('threading')

    if _threading_local:
        # Replace ``threading.local`` (and the ``_threading_local`` module, if
        # present) with our greenthread-local implementation.
        try:
            green_local_mod = _import('filament._threading_local')
        except ImportError:  # pragma: no cover
            green_local_mod = None
        if green_local_mod is not None:
            green_local = green_local_mod.local
            thr = sys.modules.get('threading')
            if thr is not None:
                patch_item(thr, 'local', green_local)
            if '_threading_local' in sys.modules or _threading_local:
                _patch_module('_threading_local', green_local_mod)

    # ``existing_locks`` (and ``logging``) run last, while we are still
    # single-threaded, converting locks that already exist.
    if existing_locks or logging:
        _patch_existing_locks(logging=logging, existing_locks=existing_locks)


# ---------------------------------------------------------------------------
# Existing-lock conversion  (eventlet issue #137 mitigation)
# ---------------------------------------------------------------------------

def _patch_existing_locks(logging=True, existing_locks=True):
    """Convert locks created *before* patching into cooperative locks.

    Why this exists
    ---------------
    When you monkey-patch a running process, ``threading.Lock``/``RLock`` from
    that point on hand back filament's cooperative locks.  But any lock objects
    that were *already constructed* -- most importantly the module-level lock in
    the stdlib ``logging`` module (``logging._lock``) and the per-handler
    ``Handler.lock`` -- are still the original OS-thread locks.

    This is the exact footgun behind **eventlet bitbucket issue #137**: code
    that logs from a real OS thread pool (or otherwise touches ``logging``)
    while holding a *native* lock can deadlock the hub, because a greenlet that
    blocks on that native lock never yields control back to the scheduler, so
    the greenlet that would release it never runs.  gevent/eventlet both fix
    this by walking existing locks and swapping in cooperative ones *while the
    process is still single-threaded* (so the swap itself is race-free).

    What we do
    ----------
    * ``logging``: rebuild ``logging._lock`` and every handler's ``.lock`` as
      cooperative locks.  ``logging.Handler.createLock`` allocates
      ``threading.RLock()`` -- which, once ``threading`` is patched, is
      filament's cooperative RLock -- so we just call it again.
    * ``existing_locks``: additionally sweep ``gc.get_objects()`` for lock
      instances.  CPython's default ``_thread.lock`` / ``_thread.RLock`` are
      C-level objects whose ``__class__`` cannot be reassigned, so those cannot
      be mutated in place; we can, however, swap the *pure-Python* RLock
      instances (``threading._PyRLock``) that gevent-style code may create.
      For the C-level ones we rely on the explicit ``logging`` fix above, which
      is the one that actually matters for #137.
    """
    # --- The important, always-safe part: fix logging's locks. -------------
    if logging:
        try:
            logging_mod = _import('logging')
        except ImportError:  # pragma: no cover
            logging_mod = None
        if logging_mod is not None:
            threading_mod = sys.modules.get('threading') or _import('threading')

            # The logging module imported ``threading`` at *its* import time and
            # holds its own reference (``logging.threading``).  That reference
            # still points at the original module, so ``Handler.createLock`` --
            # which does ``self.lock = threading.RLock()`` -- would keep minting
            # NATIVE locks.  Re-point it at the green threading module so any
            # future handler locks are cooperative too.
            if getattr(logging_mod, 'threading', None) is not None:
                logging_mod.threading = threading_mod

            # Module-level lock guarding logging's shared state.  Rebuild it as
            # a cooperative RLock.  (Assigning a fresh object is safe here
            # because we are single-threaded during patch_all.)
            if hasattr(logging_mod, '_lock'):
                # A fresh unlocked cooperative RLock is correct during
                # single-threaded startup.
                logging_mod._lock = threading_mod.RLock()

            # Every handler owns its own ``.lock``.  ``_handlerList`` holds
            # weakrefs (Py3) or handlers (Py2); ``_handlers`` is a dict/keys.
            # Assign a fresh cooperative RLock directly (rather than calling
            # createLock(), which historically produced native locks when the
            # module's threading reference had not yet been repointed).
            for handler in _iter_logging_handlers(logging_mod):
                try:
                    handler.lock = threading_mod.RLock()
                except Exception:
                    # Never let a misbehaving handler abort patching.
                    pass

    # --- Best-effort general sweep of already-created locks. ---------------
    if existing_locks:
        _sweep_existing_python_locks()


def _iter_logging_handlers(logging_mod):
    """Yield every live ``logging.Handler`` instance we can find."""
    seen = set()
    # ``_handlerList`` is a list of weakrefs on Py3 (of handlers on Py2).
    for ref in list(getattr(logging_mod, '_handlerList', []) or []):
        handler = ref() if callable(ref) else ref
        if handler is not None and id(handler) not in seen:
            seen.add(id(handler))
            yield handler
    # ``_handlers`` maps name/handler on some versions.
    handlers = getattr(logging_mod, '_handlers', None)
    if handlers:
        try:
            values = list(handlers.values())
        except AttributeError:
            values = list(handlers)
        for handler in values:
            if handler is not None and id(handler) not in seen:
                seen.add(id(handler))
                yield handler


def _sweep_existing_python_locks():
    """Swap pure-Python RLock instances found via ``gc`` to cooperative ones.

    CPython's C-level locks cannot have their ``__class__`` reassigned, so this
    only affects the pure-Python ``threading._PyRLock`` variety.  It is a
    best-effort convenience; the load-bearing #137 fix is the explicit logging
    handling above.
    """
    threading_mod = sys.modules.get('threading')
    if threading_mod is None:
        return
    py_rlock = getattr(threading_mod, '_PyRLock', None)
    if py_rlock is None:
        return
    green_rlock = getattr(threading_mod, 'RLock', None)
    if green_rlock is None:
        return
    # If RLock is not a reassignable Python class, we cannot swap __class__.
    if not isinstance(green_rlock, type):
        return
    for obj in gc.get_objects():
        try:
            if type(obj) is py_rlock and not isinstance(obj, green_rlock):
                # Layout-compatible reassignment is only valid between two
                # pure-Python classes.  Guard with a try -- if CPython refuses,
                # we simply leave the lock as-is.
                try:
                    obj.__class__ = green_rlock
                except TypeError:
                    pass
        except ReferenceError:
            # Weakproxies etc. can raise when inspected; ignore them.
            continue


# ---------------------------------------------------------------------------
# The big hammer
# ---------------------------------------------------------------------------

def patch_all(socket=True, dns=True, time=True, select=True, thread=True,
              os=True, ssl=True, subprocess=True, queue=True, aggressive=True,
              **kw):
    """Green everything (the ``gevent.monkey.patch_all`` equivalent).

    Each keyword toggles one subsystem.  Order matters a little: we green the
    thread machinery *before* touching ``logging``'s existing locks so that the
    replacement locks handed out are already cooperative.

    Unknown keywords are accepted (and ignored) via ``**kw`` so callers written
    against gevent's richer signature don't blow up.
    """
    # Low-level + high-level threading, plus the existing-lock conversion that
    # guards against the #137 logging deadlock.
    if thread:
        patch_thread(logging=kw.get('logging', True),
                     existing_locks=kw.get('existing_locks', True))

    if queue:
        patch_queue()
    if time:
        patch_time()
    if os:
        patch_os()
    if select:
        patch_select()
    if socket:
        patch_socket(dns=dns, aggressive=aggressive)
    elif dns:
        patch_dns()
    if ssl:
        patch_ssl()
    if subprocess:
        patch_subprocess()


# ---------------------------------------------------------------------------
# Backwards-compatible marker-driven API (kept working, now Py2/3 correct)
# ---------------------------------------------------------------------------

def _get_modules_to_patch(modules):
    """Resolve a list of green submodule names to (target, green) pairs."""
    module_pairs = []
    for x in modules:
        source = 'filament.' + x
        green = _import(source)
        # __filament__ marker must exist on a green module.
        target = green.__filament__.get('patch', x)
        if is_module_patched(target):
            continue  # already patched -> skip (idempotent)
        module_pairs.append((target, green))
    return module_pairs


def patch_modules(modules):
    """Patch a collection of green submodules by name (marker-driven)."""
    if isinstance(modules, _Iterable) and not _util._is_str(modules):
        modules = list(modules)
    else:
        modules = [modules]

    for target, green in _get_modules_to_patch(modules):
        _patch_module(target, green)
