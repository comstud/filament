"""
Filament exception module.  Exceptions that Filament may raise are
defined here.
"""


class Timeout(BaseException):
    """Timeout has occurred.

    A ``BaseException`` (not ``Exception``) exactly like gevent's and
    eventlet's Timeout, so that application-level ``except Exception:``
    handlers cannot accidentally swallow a timeout meant for an outer
    ``with Timeout(...):`` block.
    """
    pass


class PatcherItemNotFound(Exception):
    """Item not found when patching."""
    pass
