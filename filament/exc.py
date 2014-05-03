"""
Filament exception module.  Exceptions that Filament may raise are
defined here.
"""


class Timeout(Exception):
    """Timeout has occurred."""
    pass


class PatcherItemNotFound(Exception):
    """Item not found when patching."""
    pass
