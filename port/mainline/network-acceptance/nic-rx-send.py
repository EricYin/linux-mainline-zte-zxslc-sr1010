#!/usr/bin/env python3
"""Send eight deterministic streams and require the board's byte-verifier ACK."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import struct
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=49300)
    parser.add_argument("--bytes", type=int, default=8388608)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--chunk", type=int, default=65536)
    parser.add_argument("--delay", type=float, default=0)
    args = parser.parse_args()
    pattern = bytes(range(251)) * 263
    connected = threading.Barrier(9)
    start = threading.Event()

    def send(ident):
        with socket.create_connection((args.host, args.port), timeout=15) as sock:
            connected.wait(timeout=15)
            if not start.wait(timeout=15):
                raise TimeoutError("sender was not released")
            sock.sendall(struct.pack("!II", ident, args.bytes))
            count = 0
            while count < args.bytes:
                size = min(args.chunk, 65536, args.bytes - count)
                phase = (ident * 31 + count) % 251
                sock.sendall(pattern[phase:phase + size])
                count += size
                if args.delay:
                    time.sleep(args.delay)
            sock.shutdown(socket.SHUT_WR)
            reply = b""
            while len(reply) < 2:
                part = sock.recv(2 - len(reply))
                if not part:
                    break
                reply += part
            return {"id": ident, "bytes": count,
                    "result": "PASS" if reply == b"OK" else "FAIL"}

    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(send, ident) for ident in range(8)]
        connected.wait(timeout=15)
        if args.wait:
            print("READY", flush=True)
            if not sys.stdin.readline():
                raise RuntimeError("missing start signal")
        start.set()
        result = [job.result() for job in jobs]
    print(json.dumps(result))
    return 0 if all(row["result"] == "PASS" for row in result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
