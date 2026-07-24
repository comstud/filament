# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.pool
===========================

Drop-in replacement for ``gevent.pool`` (injected as
``sys.modules['gevent.pool']``).

``Group`` and ``Pool`` are faithful mappings onto filament's native
:class:`filament.Group` / :class:`filament.Pool`, which already carry the
gevent-shaped ``spawn`` / ``join`` / ``kill`` / ``map`` / ``imap`` API.

One adaptation: gevent's ``Pool.spawn`` returns a ``gevent.Greenlet``.  Our
filament Pool returns a raw filament greenthread.  For the common
spawn-then-join / imap usage that difference is invisible; where a caller needs
the gevent Greenlet API on the returned object, use :class:`Greenlet` directly.
"""

from __future__ import absolute_import

import filament

# Faithful mappings -- filament.Group / filament.Pool already expose the gevent
# Group/Pool method set (spawn, spawn_n, join, kill, map, imap, imap_unordered,
# free_count, wait_available, ...).
Group = filament.Group
Pool = filament.Pool

__all__ = ["Group", "Pool"]
