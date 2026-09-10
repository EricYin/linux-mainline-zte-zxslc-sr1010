#!/usr/bin/env python3
"""Inject exact checksum test frames through libpcap, without host offloads."""

import argparse
import ctypes
import ctypes.util
import ipaddress
import json
import os
import struct
import time


def checksum(data):
    if len(data) & 1:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def frame(src_mac, dst_mac, src, dst, proto, port, bad_l4=False,
          bad_ip=False, zero_udp=False):
    source = ipaddress.ip_address(src)
    destination = ipaddress.ip_address(dst)
    payload = struct.pack("!I", port) + bytes(range(64))
    if proto == 17:
        transport = struct.pack("!HHHH", port, 49000, 8 + len(payload), 0)
        csum_offset = 6
    else:
        transport = struct.pack("!HHIIBBHHH", port, 49000, port, 0,
                                5 << 4, 2, 4096, 0, 0)
        csum_offset = 16
    transport += payload
    if source.version == 4:
        pseudo = source.packed + destination.packed + struct.pack(
            "!BBH", 0, proto, len(transport))
    else:
        pseudo = source.packed + destination.packed + struct.pack(
            "!I3xB", len(transport), proto)
    value = checksum(pseudo + transport)
    if proto == 17 and value == 0:
        value = 0xffff
    if bad_l4:
        value ^= 0x1234
    if zero_udp:
        value = 0
    transport = (transport[:csum_offset] + struct.pack("!H", value)
                 + transport[csum_offset + 2:])
    if source.version == 4:
        network = struct.pack("!BBHHHBBH4s4s", 0x45, 0,
                              20 + len(transport), port, 0x4000,
                              64, proto, 0, source.packed, destination.packed)
        value = checksum(network) ^ (0x1234 if bad_ip else 0)
        network = network[:10] + struct.pack("!H", value) + network[12:]
        ethertype = 0x0800
    else:
        network = struct.pack("!IHBB16s16s", 6 << 28, len(transport),
                              proto, 64, source.packed, destination.packed)
        ethertype = 0x86dd
    ethernet = (bytes.fromhex(dst_mac.replace(":", ""))
                + bytes.fromhex(src_mac.replace(":", ""))
                + struct.pack("!H", ethertype))
    return ethernet + network + transport


def transform(packet, kind):
    ethernet, network = packet[:14], packet[14:]
    version = network[0] >> 4
    bad_ip = version == 4 and checksum(network[:(network[0] & 15) * 4]) != 0
    if kind == "vlan":
        return [ethernet[:12] + struct.pack("!HH", 0x8100, 100)
                + packet[12:]]
    if kind == "qinq":
        return [ethernet[:12] + struct.pack("!HHHH", 0x88a8, 100, 0x8100, 200)
                + packet[12:]]
    if kind == "pppoe":
        header = struct.pack("!BBHHH", 0x11, 0, 1, len(network) + 2,
                             0x21 if version == 4 else 0x57)
        return [ethernet[:12] + b"\x88\x64" + header + network]
    if kind == "options":
        if version == 4:
            header = bytearray(network[:20] + b"\x01\x01\x01\x00")
            header[0] = 0x46
            header[2:4] = struct.pack("!H", len(network) + 4)
            header[10:12] = b"\0\0"
            header[10:12] = struct.pack("!H", checksum(bytes(header))
                                       ^ (0x1234 if bad_ip else 0))
            return [ethernet + header + network[20:]]
        header = bytearray(network[:40])
        header[4:6] = struct.pack("!H", len(network) - 40 + 8)
        header[6] = 0
        extension = bytes([network[6], 0, 1, 4, 0, 0, 0, 0])
        return [ethernet + header + extension + network[40:]]
    if kind == "fragment":
        payload = network[20 if version == 4 else 40:]
        parts = [(0, payload[:32], True), (32, payload[32:], False)]
        frames = []
        for offset, data, more in parts:
            if version == 4:
                header = bytearray(network[:20])
                header[2:4] = struct.pack("!H", len(header) + len(data))
                header[6:8] = struct.pack("!H", offset // 8 | (0x2000 if more else 0))
                header[10:12] = b"\0\0"
                header[10:12] = struct.pack("!H", checksum(bytes(header))
                                           ^ (0x1234 if bad_ip else 0))
            else:
                header = bytearray(network[:40])
                header[4:6] = struct.pack("!H", 8 + len(data))
                header[6] = 44
                header += struct.pack("!BBHI", network[6], 0,
                                      offset | int(more),
                                      int.from_bytes(payload[:2], "big"))
            frames.append(ethernet + header + data)
        return frames
    return [packet]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default="en8")
    parser.add_argument("--src-mac", default="00:e0:41:68:0d:86")
    parser.add_argument("--dst-mac", required=True)
    parser.add_argument("--src", default="192.168.1.100")
    parser.add_argument("--dst", default="192.168.1.1")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--port-base", type=int, default=48000)
    parser.add_argument("--format", choices=["plain", "vlan", "qinq", "pppoe",
                                             "options", "fragment"],
                        default="plain")
    parser.add_argument("--case", choices=["tcp-good", "tcp-bad", "udp-good",
                                           "udp-bad", "udp-zero", "ip-bad"])
    args = parser.parse_args()
    if os.name == "nt":
        npcap = os.path.join(os.environ["SystemRoot"], "System32", "Npcap")
        dll_dir = os.add_dll_directory(npcap)
        pcap = ctypes.CDLL(os.path.join(npcap, "wpcap.dll"))
    else:
        pcap = ctypes.CDLL(ctypes.util.find_library("pcap"))
    pcap.pcap_open_live.argtypes = [ctypes.c_char_p, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_char_p]
    pcap.pcap_open_live.restype = ctypes.c_void_p
    pcap.pcap_sendpacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_int]
    pcap.pcap_sendpacket.restype = ctypes.c_int
    pcap.pcap_geterr.argtypes = [ctypes.c_void_p]
    pcap.pcap_geterr.restype = ctypes.c_char_p
    pcap.pcap_close.argtypes = [ctypes.c_void_p]
    error = ctypes.create_string_buffer(256)
    handle = pcap.pcap_open_live(args.interface.encode(), 65535, 0, 100, error)
    if not handle:
        raise RuntimeError(error.value.decode())
    cases = [("tcp-good", 6, {}), ("tcp-bad", 6, {"bad_l4": True}),
             ("udp-good", 17, {}), ("udp-bad", 17, {"bad_l4": True}),
             ("udp-zero", 17, {"zero_udp": True})]
    if ipaddress.ip_address(args.src).version == 4:
        cases.append(("ip-bad", 17, {"bad_ip": True}))
    try:
        for repeat in range(args.repeat):
            for index, (name, proto, options) in enumerate(cases):
                if args.case and args.case != name:
                    continue
                port = args.port_base + index
                packet = frame(args.src_mac, args.dst_mac, args.src, args.dst,
                               proto, port, **options)
                packets = transform(packet, args.format)
                for part in packets:
                    if pcap.pcap_sendpacket(handle, part, len(part)):
                        raise RuntimeError(pcap.pcap_geterr(handle).decode())
                print(json.dumps({"case": name, "src_port": port,
                                  "bytes": [len(p) for p in packets],
                                  "format": args.format, "repeat": repeat}),
                      flush=True)
                time.sleep(0.2)
    finally:
        pcap.pcap_close(handle)


if __name__ == "__main__":
    main()
