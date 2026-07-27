# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.pywsgi
=============================

Drop-in-ish replacement for ``gevent.pywsgi`` (injected as
``sys.modules['gevent.pywsgi']``).

Implements a small but genuinely WORKING WSGI/HTTP-1.1 server on top of
:class:`filament.gevent_compat.server.StreamServer`.  It parses request lines
and headers, builds a PEP-3333 WSGI ``environ``, invokes the application, and
writes back a well-formed HTTP response -- with persistent connections, so a
client that reuses its socket (every ``requests.Session``, every browser) is
served the way gevent would serve it.

Response framing follows gevent's rules:
  * the app's own ``Content-Length`` is honoured and the connection kept alive;
  * an HTTP/1.1 response without ``Content-Length`` is sent chunked;
  * anything else (HTTP/1.0 without a length, ``Connection: close``, a request
    body the app left unread) ends with the connection closed.

Deliberate simplifications (documented limits vs. gevent's full pywsgi):
  * No 100-continue handling, no HTTP/2, no pipelining beyond serving requests
    one after another on the same connection.
  * Minimal error handling: a malformed request line yields a 400 and closes.
  * The access log line is close to gevent's but not byte-identical.

Byte discipline: everything on the wire is bytes.  Header names/values in the
WSGI ``environ`` are native ``str`` (decoded latin-1 on Py3, already str on
Py2), per the WSGI spec; the response status/headers from the app are encoded
latin-1 before being written.
"""

from __future__ import absolute_import

import sys
import time

from filament.gevent_compat.server import StreamServer

# WSGI wants native str in environ.  On Py3 the wire bytes are decoded latin-1;
# on Py2 native str *is* bytes so decoding is a no-op passthrough.
_PY3 = sys.version_info[0] >= 3

if _PY3:
    from urllib.parse import unquote as _unquote_compat

    def _unquote_latin1(s):
        # gevent decodes percent-escapes as latin-1 (raw bytes semantics).
        return _unquote_compat(s, encoding="latin-1")
else:  # pragma: no cover - Python 2
    from urllib import unquote as _unquote_latin1  # noqa: F401


class Input(object):
    """
    Request body reader (gevent.pywsgi.Input shape).

    Wraps the connection's buffered reader so that ``read()`` with no argument
    returns exactly the request body instead of blocking until the client
    closes the connection.  Handles both ``Content-Length`` bodies and chunked
    request transfer-encoding; :meth:`exhaust` drains whatever the application
    did not read, which is what makes it safe to reuse the connection.
    """

    def __init__(self, rfile, content_length, chunked_input=False):
        self.rfile = rfile
        self.content_length = content_length
        self.chunked_input = chunked_input
        self.position = 0
        self._buf = b""
        self._chunk_remaining = 0
        self._chunks_done = False

    # -- Content-Length bounded stream ---------------------------------------

    def _remaining(self):
        if self.content_length is None:
            return 0                # no body advertised: nothing to read
        return max(0, self.content_length - self.position)

    # -- chunked stream ------------------------------------------------------

    def _read_chunk_header(self):
        """Consume a chunk-size line; return the size (0 ends the body)."""
        line = self.rfile.readline()
        if not line:
            self._chunks_done = True
            return 0
        # Chunk extensions after ';' are not interpreted.
        try:
            size = int(line.split(b";", 1)[0].strip(), 16)
        except ValueError:
            self._chunks_done = True
            return 0
        if size == 0:
            # Trailer headers, if any, run up to the blank line.
            while True:
                trailer = self.rfile.readline()
                if not trailer or trailer in (b"\r\n", b"\n"):
                    break
            self._chunks_done = True
        return size

    def _read_chunked_raw(self, length):
        """Pull up to ``length`` decoded body bytes (all of it if None)."""
        parts = []
        want = length
        while want is None or want > 0:
            if self._chunk_remaining == 0:
                if self._chunks_done:
                    break
                self._chunk_remaining = self._read_chunk_header()
                if self._chunk_remaining == 0:
                    break
            take = self._chunk_remaining if want is None \
                else min(want, self._chunk_remaining)
            data = self.rfile.read(take)
            if not data:
                self._chunks_done = True
                break
            self._chunk_remaining -= len(data)
            if self._chunk_remaining == 0:
                self.rfile.read(2)          # the CRLF closing this chunk
            parts.append(data)
            if want is not None:
                want -= len(data)
        return b"".join(parts)

    # -- file-like surface ---------------------------------------------------

    def read(self, length=None):
        if self.chunked_input:
            if length is None or length < 0:
                data = self._buf + self._read_chunked_raw(None)
                self._buf = b""
            else:
                if len(self._buf) < length:
                    self._buf += self._read_chunked_raw(length - len(self._buf))
                data, self._buf = self._buf[:length], self._buf[length:]
            self.position += len(data)
            return data

        remaining = self._remaining()
        if length is None or length < 0:
            length = remaining
        else:
            length = min(length, remaining)
        if length == 0:
            return b""
        data = self.rfile.read(length)
        self.position += len(data)
        return data

    def readline(self, size=None):
        if self.chunked_input:
            while b"\n" not in self._buf:
                more = self._read_chunked_raw(4096)
                if not more:
                    break
                self._buf += more
                if size is not None and len(self._buf) >= size:
                    break
            idx = self._buf.find(b"\n")
            cut = len(self._buf) if idx < 0 else idx + 1
            if size is not None:
                cut = min(cut, size)
            line, self._buf = self._buf[:cut], self._buf[cut:]
            self.position += len(line)
            return line

        remaining = self._remaining()
        if remaining == 0:
            return b""
        line = self.rfile.readline(
            remaining if size is None else min(size, remaining))
        self.position += len(line)
        return line

    def readlines(self, hint=None):
        return list(self)

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    next = __next__            # Py2

    # -- keep-alive support --------------------------------------------------

    def exhaust(self):
        """
        Drain any body the application did not read.

        Returns True if the stream is now positioned at the start of the next
        request (so the connection may be reused), False if it could not be
        drained.
        """
        if self.chunked_input:
            while not self._chunks_done:
                if not self._read_chunked_raw(65536):
                    break
            self._buf = b""
            return self._chunks_done
        while True:
            remaining = self._remaining()
            if remaining == 0:
                return True
            data = self.rfile.read(min(remaining, 65536))
            if not data:
                return False
            self.position += len(data)


def _to_native(b):
    """bytes -> native str (latin-1 on py3, identity on py2)."""
    if _PY3 and isinstance(b, bytes):
        return b.decode("latin-1")
    return b


def _to_bytes(s):
    """native str -> bytes (latin-1 on py3, identity on py2)."""
    if isinstance(s, bytes):
        return s
    return s.encode("latin-1")


class WSGIHandler(object):
    """
    Serves one connection: parses requests off ``sock`` and drives the WSGI app.

    A fresh handler is created per connection by :class:`WSGIServer`, and
    :meth:`handle` runs until the connection is done -- so subclasses can
    override :meth:`handle` for per-connection work and :meth:`log_request` for
    per-request work, exactly as they do with gevent.
    """

    # Statuses that must never carry a body (RFC 7230 3.3.1).
    _NO_BODY_STATUSES = frozenset((204, 304))

    def __init__(self, sock, address, server, rfile=None):
        self.sock = sock
        self.address = address
        self.client_address = address
        self.server = server
        self.application = server.application
        # Buffered reader over the (cooperative) socket for line-oriented parse.
        if rfile is not None:
            self.rfile = rfile
        else:
            self.rfile = \
                sock.makefile("rb", 0) if not _PY3 else sock.makefile("rb")
        self._reset_request_state()

    def _reset_request_state(self):
        self._status = None
        self._headers = []
        self._headers_sent = False
        self._chunked_response = False
        self.close_connection = True
        self._request_keep_alive = False
        self._protocol = "HTTP/1.0"
        self.requestline = None
        self.status = None
        self.response_length = 0
        self.environ = None
        self.result = None
        self.time_start = 0.0
        self.time_finish = 0.0

    # -- request parsing -----------------------------------------------------

    def _read_request(self):
        # Read and parse the request line: e.g. b"GET /path?x=1 HTTP/1.1".
        request_line = self.rfile.readline()
        if not request_line:
            return None  # client closed with nothing
        request_line = request_line.rstrip(b"\r\n")
        if not request_line:
            return None  # stray blank line from a previous request's framing
        self.requestline = _to_native(request_line)
        parts = request_line.split(b" ")
        if len(parts) != 3:
            self._send_simple(400, b"Bad Request")
            return None
        method, path, protocol = parts

        # Split path into PATH_INFO and QUERY_STRING.
        if b"?" in path:
            raw_path, query = path.split(b"?", 1)
        else:
            raw_path, query = path, b""

        # Read headers until a blank line.
        headers = {}
        while True:
            line = self.rfile.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
            line = line.rstrip(b"\r\n")
            if b":" not in line:
                continue
            name, value = line.split(b":", 1)
            headers[_to_native(name).strip().upper()] = \
                _to_native(value).strip()

        return {
            "method": _to_native(method),
            "path": _to_native(raw_path),
            "query": _to_native(query),
            "protocol": _to_native(protocol),
            "headers": headers,
        }

    def _build_environ(self, req):
        # Assemble a PEP-3333 WSGI environ dict.
        headers = req["headers"]
        server_host, server_port = self.server.address[0], self.server.address[1]
        content_length = None
        if "CONTENT-LENGTH" in headers:
            try:
                content_length = int(headers["CONTENT-LENGTH"])
            except (TypeError, ValueError):
                content_length = None
        chunked_input = "chunked" in headers.get(
            "TRANSFER-ENCODING", "").lower()
        self._input = Input(self.rfile, content_length,
                            chunked_input=chunked_input)
        environ = {
            "REQUEST_METHOD": req["method"],
            "SCRIPT_NAME": "",
            # gevent decodes percent-escapes in the path (latin-1).
            "PATH_INFO": _unquote_latin1(req["path"]),
            "QUERY_STRING": req["query"],
            "SERVER_PROTOCOL": req["protocol"],
            "SERVER_NAME": str(server_host),
            "SERVER_PORT": str(server_port),
            "SERVER_SOFTWARE": "filament-pywsgi",
            "GATEWAY_INTERFACE": "CGI/1.1",
            "REMOTE_ADDR": str(self.address[0]) if self.address else "",
            "REMOTE_PORT": str(self.address[1]) if self.address else "",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme":
                "https" if getattr(self.server, "ssl_enabled", False)
                else "http",
            # Bounded body reader: read() returns the request body and then
            # EOF, rather than blocking until the client closes.
            "wsgi.input": self._input,
            "wsgi.errors": self.server.error_log
                if getattr(self.server, "error_log", None) is not None
                else sys.stderr,
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        # Server-level environ overrides/additions (gevent's environ= kwarg).
        extra = getattr(self.server, "environ", None)
        if extra:
            environ.update(extra)
        # Content-Type / Content-Length get un-prefixed names per WSGI.
        if "CONTENT-TYPE" in headers:
            environ["CONTENT_TYPE"] = headers["CONTENT-TYPE"]
        if "CONTENT-LENGTH" in headers:
            environ["CONTENT_LENGTH"] = headers["CONTENT-LENGTH"]
        # Every other header becomes HTTP_<NAME> with dashes -> underscores.
        for name, value in headers.items():
            if name in ("CONTENT-TYPE", "CONTENT-LENGTH"):
                continue
            environ["HTTP_" + name.replace("-", "_")] = value
        return environ

    # -- response writing ----------------------------------------------------

    def _start_response(self, status, response_headers, exc_info=None):
        # WSGI start_response: stash status/headers, return a write() callable.
        self._status = status
        self.status = status
        self._headers = list(response_headers)
        return self._write_body

    def _status_code(self):
        try:
            return int(str(self._status).split(" ", 1)[0])
        except (TypeError, ValueError):     # pragma: no cover - malformed app
            return 0

    def _decide_framing(self):
        """
        Pick the response framing, gevent-style, and whether the connection
        survives it.  Runs once the app has called ``start_response``, since
        the app's own ``Content-Length`` is what decides it.

        An HTTP/1.1 response without a length is chunked; otherwise the body is
        delimited by closing the connection.
        """
        keep_alive = self._request_keep_alive
        has_length = any(name.lower() == "content-length"
                         for name, _ in self._headers)
        if self._status_code() in self._NO_BODY_STATUSES or has_length:
            self._chunked_response = False
        elif keep_alive and self._protocol == "HTTP/1.1":
            self._chunked_response = True
        else:
            self._chunked_response = False
            keep_alive = False                 # close delimits the body
        self.close_connection = not keep_alive

    def _send_headers(self):
        if self._headers_sent:
            return
        if self._status is None:            # app never called start_response
            self._status = "500 Internal Server Error"
            self._headers = []
        self._decide_framing()
        self._headers_sent = True
        lines = [b"HTTP/1.1 " + _to_bytes(self._status)]
        for name, value in self._headers:
            lines.append(_to_bytes(name) + b": " + _to_bytes(value))
        if self._chunked_response:
            lines.append(b"Transfer-Encoding: chunked")
        if self.close_connection:
            lines.append(b"Connection: close")
        elif self._protocol == "HTTP/1.0":
            # 1.0 clients only reuse the connection if we say so explicitly.
            lines.append(b"Connection: keep-alive")
        lines.append(b"")
        lines.append(b"")
        self.sock.sendall(b"\r\n".join(lines))

    def _write_body(self, data):
        # The write() callable handed to legacy apps.
        if not self._headers_sent:
            self._send_headers()
        if not data:
            return
        data = _to_bytes(data)
        self.response_length += len(data)
        if self._chunked_response:
            self.sock.sendall(
                ("%x\r\n" % len(data)).encode("ascii") + data + b"\r\n")
        else:
            self.sock.sendall(data)

    def _finish_response(self):
        if not self._headers_sent:
            self._send_headers()
        if self._chunked_response:
            self.sock.sendall(b"0\r\n\r\n")

    # -- logging -------------------------------------------------------------

    def format_request(self):
        """The access-log line for the request just served."""
        return '%s - - "%s" %s %s %.6f' % (
            self.address[0] if self.address else "-",
            self.requestline or "-",
            (self._status or "-").split(" ", 1)[0]
            if isinstance(self._status, str) else "-",
            self.response_length,
            self.time_finish - self.time_start,
        )

    def log_request(self):
        """
        Write one access-log line to ``server.log``.

        Overridable, and overridden in the wild to count requests, so it
        stays a real method even when the server has no log.
        """
        log = getattr(self.server, "log", None)
        if log is None:
            return
        try:
            log.write(self.format_request() + "\n")
        except Exception:               # pragma: no cover - broken log sink
            pass

    # -- driver --------------------------------------------------------------

    def handle_one_request(self):
        """
        Serve a single request.  Returns True if the connection may be reused.
        """
        self._reset_request_state()
        self.time_start = time.time()
        req = self._read_request()
        if req is None:
            return False

        # RFC 7230: 1.1 defaults to keep-alive, 1.0 needs to ask for it.
        connection_hdr = req["headers"].get("CONNECTION", "").lower()
        self._protocol = protocol = req["protocol"]
        self._request_keep_alive = (
            (protocol == "HTTP/1.1" and connection_hdr != "close") or
            (protocol == "HTTP/1.0" and connection_hdr == "keep-alive"))

        self.environ = environ = self._build_environ(req)
        self.result = result = self.application(environ, self._start_response)
        try:
            for chunk in result:
                self._write_body(chunk)
            self._finish_response()
        finally:
            # Honour the WSGI close protocol if the iterable provides it.
            close = getattr(result, "close", None)
            if close is not None:
                close()
            self.time_finish = time.time()
            self.log_request()

        if self.close_connection:
            return False
        # A body the app never read would be parsed as the next request line.
        return self._input.exhaust()

    def handle(self):
        """
        Serve this connection until the client or the framing ends it.

        This is the per-connection entry point (gevent parity) -- subclasses
        override it to hook connection setup/teardown.
        """
        try:
            while self.handle_one_request():
                pass
        finally:
            try:
                self.rfile.close()
            except Exception:
                pass

    def run(self):
        """``run`` predates ``handle`` here; keep it working, via the override."""
        self.handle()

    # -- tiny helper for error responses ------------------------------------

    def _send_simple(self, code, body):
        reason = {400: b"Bad Request", 500: b"Internal Server Error"}.get(
            code, b"Error")
        payload = (b"HTTP/1.1 " + str(code).encode("ascii") + b" " + reason +
                   b"\r\nContent-Length: " +
                   str(len(body)).encode("ascii") +
                   b"\r\nConnection: close\r\n\r\n" + body)
        self.sock.sendall(payload)


class _NoopLog(object):
    """Discard-everything log sink (gevent uses a similar null log)."""

    def write(self, *args, **kwargs):
        pass

    def writelines(self, *args, **kwargs):
        pass

    def flush(self):
        pass


class WSGIServer(StreamServer):
    """
    Cooperative WSGI/HTTP-1.1 server (gevent.pywsgi.WSGIServer).

    Construct with ``WSGIServer((host, port), app)`` then call
    :meth:`serve_forever` (or :meth:`start`).  Each accepted connection is
    handled by a :class:`WSGIHandler` per the StreamServer spawn strategy.

    The positional parameter order matches gevent's:
    ``(listener, application, backlog, spawn, log, error_log, handler_class,
    environ, **ssl_args)``.  ``ssl_args`` (keyfile/certfile) and ``backlog``
    are forwarded to :class:`StreamServer` -- never silently dropped.
    """

    def __init__(self, listener, application=None, backlog=None,
                 spawn='default', log='default', error_log='default',
                 handler_class=None, environ=None, **ssl_args):
        self.application = application
        self.handler_class = handler_class or WSGIHandler
        # gevent: 'default' logs access to stderr and errors to stderr; None
        # silences.  Both attributes always exist on the server object.
        self.log = sys.stderr if log == 'default' else (log or _NoopLog())
        self.error_log = sys.stderr if error_log == 'default' \
            else (error_log or _NoopLog())
        self.environ = dict(environ) if environ else {}
        self.ssl_enabled = bool(
            ssl_args.get("keyfile") or ssl_args.get("certfile"))
        StreamServer.__init__(self, listener, handle=self._handle_wsgi,
                              backlog=backlog, spawn=spawn, **ssl_args)

    def _handle_wsgi(self, sock, address):
        handler = self.handler_class(sock, address, self)
        handler.handle()
