import sys

from filament import exc
from filament import _util

_patch_items = {
        'socket': { 'socket': 'filament.socket', 'ssl': 'filament.ssl' }}


class Patcher(object):
    def __init__(self):
        self.orig_objects = {}

    def _find_attr(self, name):
        """Find an attribute and its source object.

        Returns a tuple of (source obj, attr obj, attr name)

        If 'name' is a module, return None, sys.modules[name], None
        raise RuntimeError for now on NotFound.
        """

        try:
            __import__(name)
            return None, sys.modules[name], None
        except ImportError:
            pass

        # find the module
        parts = name.split('.')
        for index in range(1, len(parts)):
            start = '.'.join(parts[:-index])
            try:
                __import__(start)
                break
            except ImportError:
                pass
        else:
            raise exc.PatcherItemNotFound("Couldn't find %s" % name)

        end_parts = parts[-index:]
        src_obj = sys.modules[start]
        obj = None
        for part in end_parts:
            if obj is not None:
                src_obj = obj
            try:
                obj = getattr(src_obj, part)
            except AttributeError:
                raise exc.PatcherItemNotFound("Couldn't find %s" % name)
        return src_obj, obj, part

    def patch_attr(self, name, new_object):
        # 'name' should contain full module path to target
        if name in self.orig_objects:
            return self.orig_objects[name][1]

        src, orig, attr_name = self._find_attr(name)
        if attr_name is None:
            # 'name' must be a module
            sys.modules[name] = new_object
        else:
            setattr(src, attr_name, new_object)
        self.orig_objects[name] = (src, orig)
        return orig

    def patch_item(self, source, target):
        pass

    def get_original(self, name):
        try:
            return self.orig_objects[name][1]
        except KeyError:
            return self._find_attr(name)[1]


_PATCHER = Patcher()


def patch(name, new_object):
    return _PATCHER.patch(name, new_object)


def get_original(name):
    return _PATCHER.get_original(name)


def patch_all():
    for name in _patch_items:
        for target in _patch_items[name]:
            pass


    from filament.patcher import socket
    from filament.patcher import os
    socket._patch()
    os._patch()
