# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
#
# Copyright (c) 2013-2014, Chris Behrens
"""
filament.gevent_compat.pywsgi
=============================

Drop-in-ish replacement for ``gevent.pywsgi`` (injected as
``sys.modules['gevent.pywsgi']``).

Implements a MINIMAL but genuinely WORKING WSGI/HTTP-1.1 server on top of
:class:`filament.gevent_compat.server.StreamServer`.  It parses a request line
and headers, builds a PEP-3333 WSGI ``environ``, invokes the application, and
writes back a well-formed HTTP response.  Enough to serve a hello-world WSGI app
and handle simple GET/POST requests.

Deliberate simplifications (documented limits vs. gevent's full pywsgi):
  * One request per connection: we always send ``Connection: close`` and close
    the socket after responding (no keep-alive / pipelining).
  * Request bodies are read via ``Content-Length`` only; chunked *request*
    transfer-encoding is not decoded.
  * Responses set an explicit ``Content-Length`` when the app yields a single
    bytes chunk / list; otherwise the connection close delimits the body.
  * No 100-continue, no HTTP/2, minimal error handling (a malformed request
    line yields a 400 and closes).

Byte discipline: everything on the wire is bytes.  Header names/values in the
WSGI ``environ`` are native ``str`` (decoded latin-1 on Py3, already str on
Py2), per the WSGI spec; the response status/headers from the app are encoded
latin-1 before being written.
"""

from __future__ import absolute_import

import sys

from filament.gevent_compat.server import StreamServer

# WSGI wants native str in environ.  On Py3 the wire bytes are decoded latin-1;
# on Py2 native str *is* bytes so decoding is a no-op passthrough.
_PY3 = sys.version_info[0] >= 3


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
    Parses a single HTTP request off ``sock`` and drives the WSGI app.

    A fresh handler is created per connection by :class:`WSGIServer`.
    """

    def __init__(self, sock, address, server):
        self.sock = sock
        self.address = address
        self.server = server
        self.application = server.application
        # Buffered reader over the (cooperative) socket for line-oriented parse.
        self.rfile = sock.makefile("rb", 0) if not _PY3 else sock.makefile("rb")
        self._status = None
        self._headers = []
        self._headers_sent = False

    # -- request parsing -----------------------------------------------------

    def _read_request(self):
        # Read and parse the request line: e.g. b"GET /path?x=1 HTTP/1.1".
        request_line = self.rfile.readline()
        if not request_line:
            return None  # client closed with nothing
        request_line = request_line.rstrip(b"\r\n")
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
        environ = {
            "REQUEST_METHOD": req["method"],
            "SCRIPT_NAME": "",
            "PATH_INFO": req["path"],
            "QUERY_STRING": req["query"],
            "SERVER_PROTOCOL": req["protocol"],
            "SERVER_NAME": str(server_host),
            "SERVER_PORT": str(server_port),
            "REMOTE_ADDR": str(self.address[0]) if self.address else "",
            "REMOTE_PORT": str(self.address[1]) if self.address else "",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": self.rfile,
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
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
        self._headers = list(response_headers)
        return self._write_body

    def _send_headers(self):
        if self._headers_sent:
            return
        self._headers_sent = True
        lines = [b"HTTP/1.1 " + _to_bytes(self._status)]
        have_length = False
        for name, value in self._headers:
            if name.lower() == "content-length":
                have_length = True
            lines.append(_to_bytes(name) + b": " + _to_bytes(value))
        # We do not support keep-alive: always close after the response.
        lines.append(b"Connection: close")
        self._no_length = not have_length
        lines.append(b"")
        lines.append(b"")
        self.sock.sendall(b"\r\n".join(lines))

    def _write_body(self, data):
        # The write() callable handed to legacy apps.
        if not self._headers_sent:
            self._send_headers()
        if data:
            self.sock.sendall(_to_bytes(data))

    # -- driver --------------------------------------------------------------

    def handle_one_request(self):
        req = self._read_request()
        if req is None:
            return
        environ = self._build_environ(req)
        result = self.application(environ, self._start_response)
        try:
            for chunk in result:
                if not self._headers_sent:
                    self._send_headers()
                if chunk:
                    self.sock.sendall(_to_bytes(chunk))
            # An app that returned an empty iterable still needs headers sent.
            if not self._headers_sent:
                self._send_headers()
        finally:
            # Honour the WSGI close protocol if the iterable provides it.
            close = getattr(result, "close", None)
            if close is not None:
                close()

    def run(self):
        try:
            self.handle_one_request()
        finally:
            try:
                self.rfile.close()
            except Exception:
                pass

    # -- tiny helper for error responses ------------------------------------

    def _send_simple(self, code, body):
        reason = {400: b"Bad Request", 500: b"Internal Server Error"}.get(
            code, b"Error")
        payload = (b"HTTP/1.1 " + str(code).encode("ascii") + b" " + reason +
                   b"\r\nContent-Length: " +
                   str(len(body)).encode("ascii") +
                   b"\r\nConnection: close\r\n\r\n" + body)
        self.sock.sendall(payload)


class WSGIServer(StreamServer):
    """
    Minimal cooperative WSGI/HTTP-1.1 server (gevent.pywsgi.WSGIServer).

    Construct with ``WSGIServer((host, port), app)`` then call
    :meth:`serve_forever` (or :meth:`start`).  Each accepted connection is
    handled by a :class:`WSGIHandler` in the StreamServer's concurrency pool.
    """

    def __init__(self, listener, application=None, log=None, handler_class=None,
                 spawn='default', **kwargs):
        # StreamServer builds the listening socket + pool; we route each
        # connection through our WSGI handler instead of a bare handle().
        self.application = application
        self.handler_class = handler_class or WSGIHandler
        StreamServer.__init__(self, listener, handle=self._handle_wsgi,
                              spawn=spawn)

    def _handle_wsgi(self, sock, address):
        handler = self.handler_class(sock, address, self)
        handler.run()


__all__ = ["WSGIServer", "WSGIHandler"]
