# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.event
============================

Drop-in replacement for ``gevent.event`` (injected as
``sys.modules['gevent.event']``).

Both classes are faithful mappings onto filament's native primitives:

  * ``Event``       -> :class:`filament.Event` (settable flag, set/clear/wait).
  * ``AsyncResult`` -> :class:`filament.AsyncResult` (one-shot value/exception
                       future with set/set_exception/get/wait/link).
"""

from __future__ import absolute_import

import filament

Event = filament.Event
AsyncResult = filament.AsyncResult

__all__ = ["Event", "AsyncResult"]
