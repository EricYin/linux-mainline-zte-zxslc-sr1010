#!/usr/bin/env python3
"""Verify concurrent nic-tx-stream clients, including every payload byte."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import socket
import struct


def verify(connection):
    with connection:
        connection.settimeout(20)
        header = bytearray()
        while len(header) < 8:
            data = connection.recv(8 - len(header))
            if not data:
                raise RuntimeError("incomplete stream header")
            header.extend(data)
        ident, size = struct.unpack("!II", header)
        pattern = bytes(range(251)) * 263
        count = 0
        errors = 0
        digest = hashlib.sha256()
        while data := connection.recv(65536):
            phase = (ident * 31 + count) % 251
            errors += sum(a != b for a, b in zip(data, pattern[phase:phase + len(data)]))
            digest.update(data)
            count += len(data)
        valid = not errors and count == size and size > 0
        connection.sendall(b"OK" if valid else b"NO")
        return {"id": ident, "bytes": count, "expected_bytes": size,
                "bad_bytes": errors, "sha256": digest.hexdigest(),
                "result": "PASS" if valid else "FAIL"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="192.168.1.100")
    parser.add_argument("--port", type=int, default=49020)
    parser.add_argument("--flows", type=int, default=8)
    args = parser.parse_args()
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=args.flows) as pool:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((args.bind, args.port))
        listener.listen(args.flows)
        listener.settimeout(40)
        print("READY", args.bind, args.port, args.flows, flush=True)
        jobs = [pool.submit(verify, listener.accept()[0]) for _ in range(args.flows)]
        results = sorted((job.result() for job in jobs), key=lambda item: item["id"])
    print(json.dumps(results, indent=2), flush=True)
    if any(item["result"] != "PASS" for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
