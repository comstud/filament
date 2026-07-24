# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.eventlet_compat.event
==============================

Drop-in replacement for ``eventlet.event`` (injected as
``sys.modules['eventlet.event']``).

eventlet's ``event.Event`` is a *one-shot* future: ``send`` a value (or
``send_exception``), ``wait`` for it, ``ready`` to poll, ``reset`` to reuse.
That is exactly the contract of :class:`filament.AsyncResult` (which already
carries the eventlet aliases ``send`` / ``send_exception`` / ``reset``), so we
simply expose it under the eventlet name -- a faithful mapping, no adaptation
needed.
"""

from __future__ import absolute_import

import filament

# eventlet.event.Event == filament.AsyncResult (one-shot send/wait/reset).
Event = filament.AsyncResult

__all__ = ["Event"]
