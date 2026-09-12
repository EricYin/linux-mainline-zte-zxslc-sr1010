#!/usr/bin/env python3
"""Send bounded, interleaved UDP flows with checked bytes and sequence numbers."""

import argparse
import json
import socket
import struct
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    parser.add_argument("--first", type=int, default=0)
    parser.add_argument("--flows", type=int, default=8)
    parser.add_argument("--count", type=int, default=512)
    args = parser.parse_args()
    if not (0 <= args.first < args.first + args.flows <= 8 and
            1 <= args.count <= 512):
        parser.error("flows must fit 0..7; count must fit 1..512")
    sockets = []
    family, _, _, _, destination = socket.getaddrinfo(
        args.destination, 49000, type=socket.SOCK_DGRAM)[0]
    try:
        for flow in range(args.first, args.first + args.flows):
            sock = socket.socket(family, socket.SOCK_DGRAM)
            source = socket.getaddrinfo(args.source, 48000 + flow, family, socket.SOCK_DGRAM)[0][4]
            sock.bind(source)
            sockets.append((flow, sock))
        for sequence in range(args.count):
            for flow, sock in sockets:
                payload = struct.pack("!IIII", 0x52535331, flow, sequence, args.count)
                payload += bytes((flow * 31 + sequence + offset) % 251
                                 for offset in range(16, 256))
                sock.sendto(payload, destination)
            time.sleep(0.001)
        print(json.dumps({"packets": args.flows * args.count, "bytes_per_packet": 256}))
    finally:
        for _, sock in sockets:
            sock.close()


if __name__ == "__main__":
    main()
