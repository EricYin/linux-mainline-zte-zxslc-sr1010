#!/usr/bin/env python3
"""Bounded NAT-flow echo with deterministic content checked at both endpoints."""

import argparse
import ctypes
import json
import os
import socket
import struct
import sys


def payload(sequence):
    return struct.pack("!II", 0x52534631, sequence) + bytes((sequence + i) % 251 for i in range(8, 256))


def checksum(data):
    if len(data) & 1:
        data += b"\0"
    value = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while value >> 16:
        value = (value & 0xffff) + (value >> 16)
    return ~value & 0xffff


def raw_client(router_mac, port):
    folder = os.path.join(os.environ["SystemRoot"], "System32", "Npcap")
    directory = os.add_dll_directory(folder)
    pcap = ctypes.CDLL(os.path.join(folder, "wpcap.dll"))
    pcap.pcap_open_live.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_char_p]
    pcap.pcap_open_live.restype = ctypes.c_void_p
    pcap.pcap_sendpacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    pcap.pcap_close.argtypes = [ctypes.c_void_p]
    error = ctypes.create_string_buffer(256)
    interface = rb"\Device\NPF_{908BA7F3-F82E-4E1E-92C6-573623C048F9}"
    handle = pcap.pcap_open_live(interface, 65535, 0, 100, error)
    if not handle:
        raise RuntimeError(error.value.decode())

    def send(data, sequence):
        source, destination = socket.inet_aton("192.168.5.100"), socket.inet_aton("192.168.1.100")
        udp = struct.pack("!HHHH", port, port, 8 + len(data), 0) + data
        check = checksum(source + destination + struct.pack("!BBH", 0, 17, len(udp)) + udp)
        udp = udp[:6] + struct.pack("!H", check or 0xffff) + udp[8:]
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), sequence,
                         0x4000, 64, 17, 0, source, destination)
        ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
        frame = bytes.fromhex(router_mac.replace(":", "")) + bytes.fromhex("f8893c26fe02") + b"\x08\x00" + ip + udp
        if pcap.pcap_sendpacket(handle, frame, len(frame)):
            raise OSError("Npcap send failed")

    def close():
        pcap.pcap_close(handle)
        directory.close()

    return send, close


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("server", "client"))
    parser.add_argument("--port", type=int, default=5301)
    parser.add_argument("--count", type=int, default=128)
    parser.add_argument("--router-mac")
    args = parser.parse_args()
    server = args.role == "server"
    if not server and not args.router_mac:
        parser.error("client requires --router-mac for the fixed LAN injection path")
    local = "192.168.1.100" if server else "192.168.5.100"
    expected_peer = ("192.168.1.1" if server else "192.168.1.100", args.port)
    count, bad, error = 0, 0, ""
    send, close = (None, None) if server else raw_client(args.router_mac, args.port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((local, args.port))
        sock.settimeout(3)
        print("READY", flush=True)
        if not sys.stdin.readline():
            return 2
        try:
            for sequence in range(args.count):
                expected = payload(sequence)
                if not server:
                    send(expected, sequence)
                received, peer = sock.recvfrom(4096)
                if received != expected or peer != expected_peer:
                    bad += 1
                if server:
                    sock.sendto(received, peer)
                count += 1
        except OSError as exception:
            error = str(exception)
        finally:
            if close:
                close()
    passed = count == args.count and not bad and not error
    print(json.dumps({"role": args.role, "packets": count, "bytes": count * 256,
                      "bad": bad, "error": error, "result": "PASS" if passed else "FAIL"}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
