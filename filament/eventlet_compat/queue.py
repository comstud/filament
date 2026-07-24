# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.eventlet_compat.queue
==============================

Drop-in replacement for ``eventlet.queue`` (injected as
``sys.modules['eventlet.queue']``).

eventlet's ``queue`` module offers ``Queue``, ``LifoQueue``, ``PriorityQueue``,
``Empty`` and ``Full``.  filament's cooperative C queue only provides FIFO
``Queue`` / ``SimpleQueue`` (plus ``Empty`` / ``Full``); the LIFO and priority
disciplines come from filament's pure-Python ``pyqueue`` module.  All are
cooperative (blocking get/put yield to the scheduler).
"""

from __future__ import absolute_import

import filament
from filament import pyqueue as _pyqueue

# FIFO queue + exceptions: the fast C implementation (faithful mapping).
Queue = filament.Queue
Empty = filament.Empty
Full = filament.Full

# LIFO / priority disciplines: pure-Python cooperative subclasses (faithful
# mapping -- same blocking semantics, different ordering).
LifoQueue = _pyqueue.LifoQueue
PriorityQueue = _pyqueue.PriorityQueue

__all__ = ["Queue", "LifoQueue", "PriorityQueue", "Empty", "Full"]
