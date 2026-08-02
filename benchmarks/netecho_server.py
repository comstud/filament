#!/usr/bin/env python
"""Echo server for the networked benchmark, in the framework under test.

The in-process echo benchmark in worker.py runs its client and its server on
one runtime in one process, so what it reports is a whole-runtime number -- a
fast client and a fast server are indistinguishable in it, and both compete for
the same GIL and scheduler.  This is the server half only; the client is
netecho/loadgen.go, one fixed implementation driving all three frameworks from
another machine.

    python netecho_server.py --mode filament --port 18899

Runs until killed.  Prints one READY line on stderr once it is listening, so an
orchestrator can wait for it rather than sleeping.
"""
from __future__ import print_function

import argparse
import os
import socket as _stdsocket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from worker import make_env  # noqa: E402  (needs the path insert above)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True,
                    choices=["filament", "gevent", "eventlet"])
    ap.add_argument("--port", type=int, default=18899)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--size", type=int, default=64,
                    help="expected request size; the server echoes what it "
                         "reads, so this only sizes the recv")
    ap.add_argument("--backlog", type=int, default=4096)
    args = ap.parse_args()

    env = make_env(args.mode)
    size = args.size

    def body():
        srv = env.green_socket()
        srv.setsockopt(_stdsocket.SOL_SOCKET, _stdsocket.SO_REUSEADDR, 1)
        srv.bind((args.bind, args.port))
        srv.listen(args.backlog)
        sys.stderr.write("READY mode=%s pid=%d port=%d\n"
                         % (args.mode, os.getpid(), args.port))
        sys.stderr.flush()

        def handle(conn):
            try:
                conn.setsockopt(_stdsocket.IPPROTO_TCP,
                                _stdsocket.TCP_NODELAY, 1)
            except Exception:
                pass
            try:
                while True:
                    data = conn.recv(size)
                    if not data:
                        return
                    conn.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        while True:
            conn, _addr = srv.accept()
            env.spawn(handle, conn)

    env.run(body)


if __name__ == "__main__":
    main()
