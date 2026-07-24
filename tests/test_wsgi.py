# The MIT License (MIT): http://opensource.org/licenses/mit-license.php
"""
gevent.pywsgi WSGIServer tests: serve a GET and a POST through the filament-backed
minimal WSGI server and assert the HTTP status line and body.

Run in a subprocess (the shim registers ``sys.modules['gevent']`` and the server
opens a real listening socket).
"""

from __future__ import absolute_import

from tests._helpers import run_py

_PREAMBLE = '''
import filament.gevent_compat as gc
gc.install()
import gevent
from gevent import pywsgi
import filament
import filament.socket as fsocket


def http_request(addr, raw):
    c = fsocket.create_connection(addr)
    c.sendall(raw)
    chunks = []
    while True:
        d = c.recv(4096)
        if not d:
            break
        chunks.append(d)
    c.close()
    return b"".join(chunks)


def start_server(app):
    server = pywsgi.WSGIServer(("127.0.0.1", 0), app)
    server.start()
    return server, (server.server_host, server.server_port)
'''


def _check(script, timeout=25):
    res = run_py(_PREAMBLE + script, timeout=timeout)
    assert not res.timed_out, "subprocess timed out\n" + repr(res)
    assert res.returncode == 0, repr(res)
    assert "OK" in res.stdout, repr(res)
    return res


def test_wsgi_get():
    _check('''
def app(environ, start_response):
    assert environ["REQUEST_METHOD"] == "GET"
    assert environ["PATH_INFO"] == "/hello"
    body = b"hello-get"
    start_response("200 OK", [("Content-Type", "text/plain"),
                              ("Content-Length", str(len(body)))])
    return [body]

server, addr = start_server(app)
raw = b"GET /hello HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n"
resp = http_request(addr, raw)
server.stop()
assert b"200 OK" in resp.split(b"\\r\\n")[0], resp
assert resp.endswith(b"hello-get"), resp
print("OK")
''')


def test_wsgi_post_reads_body():
    _check('''
def app(environ, start_response):
    assert environ["REQUEST_METHOD"] == "POST"
    length = int(environ.get("CONTENT_LENGTH", 0))
    data = environ["wsgi.input"].read(length)
    start_response("200 OK", [("Content-Type", "text/plain"),
                              ("Content-Length", str(len(data)))])
    return [data]

server, addr = start_server(app)
payload = b"name=filament&v=1"
raw = (b"POST /submit HTTP/1.1\\r\\nHost: x\\r\\n"
       b"Content-Length: " + str(len(payload)).encode("ascii") + b"\\r\\n"
       b"\\r\\n" + payload)
resp = http_request(addr, raw)
server.stop()
assert b"200 OK" in resp.split(b"\\r\\n")[0], resp
assert resp.endswith(payload), resp
print("OK")
''')


def test_wsgi_query_string_and_status():
    _check('''
def app(environ, start_response):
    qs = environ["QUERY_STRING"]
    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [qs.encode("ascii")]

server, addr = start_server(app)
resp = http_request(addr, b"GET /path?a=1&b=2 HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n")
server.stop()
first = resp.split(b"\\r\\n")[0]
assert b"404 Not Found" in first, resp
assert resp.endswith(b"a=1&b=2"), resp
print("OK")
''')


def test_wsgi_concurrent_requests():
    _check('''
def app(environ, start_response):
    body = environ["PATH_INFO"].encode("ascii")
    start_response("200 OK", [("Content-Length", str(len(body)))])
    return [body]

server, addr = start_server(app)

def do(i):
    raw = ("GET /p%d HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n" % i).encode("ascii")
    resp = http_request(addr, raw)
    return resp.endswith(("/p%d" % i).encode("ascii"))

gts = [filament.spawn(do, i) for i in range(20)]
results = [g.wait() for g in gts]
server.stop()
assert all(results), results
print("OK")
''', timeout=25)
