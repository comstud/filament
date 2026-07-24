"""Filament low-level I/O helpers.

This is a thin re-export shim over the C extension module '_filament.io'. It
exists so Python code can simply ``from filament import io`` (or
``from filament.io import ...``) without reaching into the '_filament' C package
directly.

Everything below comes straight from the C module. The names most relevant to
the cooperative socket/ssl layers are:

    fd_wait_read_ready(fileno, abstimeout=..., timeout_exc=...)
    fd_wait_write_ready(fileno, abstimeout=..., timeout_exc=...)
        Yield the current greenthread to the filament scheduler until the given
        file descriptor is readable/writable (or the absolute timeout fires, in
        which case timeout_exc is raised). These are what let SSL retry loops
        wait for readiness without blocking the whole process.

    abstimeout_from_timeout(timeout)
        Convert a relative timeout (seconds, or None) into the absolute-deadline
        object that fd_wait_*_ready expect.

    os_read / os_write   -- cooperative os.read/os.write equivalents.
    FDesc / IOThread     -- lower-level descriptor / io-thread objects.
    CAPI                 -- the C API capsule.
"""

from _filament.io import *  # noqa: F401,F403  (deliberate re-export)
