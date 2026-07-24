# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.eventlet_compat.timeout
================================

Drop-in replacement for ``eventlet.timeout`` (injected as
``sys.modules['eventlet.timeout']``).

eventlet's ``Timeout`` / ``with_timeout`` have the same semantics as filament's
native ones, so these are faithful re-exports.
"""

from __future__ import absolute_import

import filament

# Faithful mapping: filament.Timeout is a context-manager + exception with the
# gevent/eventlet Timeout(seconds, exception) shape, including the
# Timeout(seconds, False) silent-sentinel form eventlet also supports.
Timeout = filament.Timeout
with_timeout = filament.with_timeout

__all__ = ["Timeout", "with_timeout"]
