#!/usr/bin/env python3
"""Observe ordinary RX paths for explicit packet formats, without hash injection."""

import argparse
import ipaddress
import json
from pathlib import Path
import re
import runpy
import struct
import subprocess
import sys
import time
import urllib.request


HERE = Path(__file__).resolve().parent
NETWORK = HERE.parent / "network-acceptance"
BOARD = runpy.run_path(str(NETWORK / "nic-rx-accept.py"))["board"]
PACKETS = runpy.run_path(str(NETWORK / "nic-checksum-packets.py"))
STATS = runpy.run_path(str(NETWORK / "nic-rx-parallel.py"))["stats"]
KINDS = ("ipv4-udp", "ipv4-tcp", "ipv4-ack", "ipv4-options", "ipv4-frag-first",
         "ipv4-frag-later", "ipv6-udp", "ipv6-tcp", "ipv6-ack", "ipv6-options", "ipv6-frag-first",
         "ipv6-frag-later", "ipv4-icmp", "nonip", "arp", "vlan", "qinq", "pppoe",
         "ipv4-bad-checksum", "ipv4-ihl4", "ipv4-total-short", "ipv4-total-long",
         "ipv4-truncated", "udp-length-short", "udp-length-long", "tcp-doff4")


def make_frame(mode, kind, mac, flow_hit=False, source_port=48000,
               ipv6_source=None, ipv6_destination=None):
    network = 1 if mode == "wan" else 5
    source_mac = "00:e0:41:68:0d:86" if mode == "wan" else "f8:89:3c:26:fe:02"
    source, destination = f"192.168.{network}.100", f"192.168.{network}.1"
    if flow_hit and mode == "lan":
        destination = "192.168.1.100"
    v6 = kind.startswith("ipv6")
    if v6:
        source = ipv6_source or f"2001:db8:{network}::100"
        destination = ipv6_destination or f"2001:db8:{network}::1"
    proto = 6 if kind in ("ipv4-tcp", "ipv4-ack", "ipv6-tcp", "ipv6-ack", "tcp-doff4") else 17
    packet = bytearray(PACKETS["frame"](source_mac, mac, source, destination, proto,
                                      5301 if flow_hit else source_port)[:-28])
    transport = 54 if v6 else 34
    if proto == 17:
        packet[transport + 4:transport + 6] = struct.pack("!H", len(packet) - transport)
        if flow_hit:
            packet[transport + 2:transport + 4] = struct.pack("!H", 5301)
    if kind in ("ipv4-ack", "ipv6-ack"):
        packet[transport + 13] = 0x10
    offset = transport + (6 if proto == 17 else 16)
    packet[offset:offset + 2] = b"\0\0"
    pseudo = ((packet[22:54] + struct.pack("!I3xB", len(packet) - transport, proto))
              if v6 else packet[26:34] + struct.pack("!BBH", 0, proto, len(packet) - transport))
    packet[offset:offset + 2] = struct.pack("!H", PACKETS["checksum"](pseudo + packet[transport:]) or 0xffff)
    if v6:
        packet[18:20] = struct.pack("!H", len(packet) - 54)
    else:
        packet[16:18] = struct.pack("!H", len(packet) - 14)
        packet[24:26] = b"\0\0"
        packet[24:26] = struct.pack("!H", PACKETS["checksum"](packet[14:34]))
    if "frag" in kind:
        packet = PACKETS["transform"](bytes(packet), "fragment")[int(kind.endswith("later"))]
    elif "options" in kind:
        packet = PACKETS["transform"](bytes(packet), "options")[0]
    elif kind in ("vlan", "qinq", "pppoe"):
        packet = PACKETS["transform"](bytes(packet), kind)[0]
    elif kind == "ipv4-icmp":
        packet[23] = 1
        body = bytearray(b"\x08\0\0\0\x12\x34\0\x01" + bytes(range(40)))
        body[2:4] = struct.pack("!H", PACKETS["checksum"](body))
        packet[34:] = body
        packet[24:26] = b"\0\0"
        packet[24:26] = struct.pack("!H", PACKETS["checksum"](packet[14:34]))
    elif kind == "nonip":
        packet = packet[:12] + b"\x88\xb5" + bytes(range(64))
    elif kind == "arp":
        packet = (packet[:12] + b"\x08\x06" + struct.pack("!HHBBH", 1, 0x800, 6, 4, 1) +
                  bytes.fromhex(source_mac.replace(":", "")) + packet[26:30] + b"\0" * 6 + packet[30:34])
    elif kind == "ipv4-bad-checksum":
        packet[24] ^= 1
    elif kind in ("ipv4-ihl4", "ipv4-total-short", "ipv4-total-long"):
        if kind == "ipv4-ihl4":
            packet[14] = 0x44
        else:
            packet[16:18] = struct.pack("!H", 20 if kind.endswith("short") else 512)
        packet[24:26] = b"\0\0"
        packet[24:26] = struct.pack("!H", PACKETS["checksum"](packet[14:34]))
    elif kind == "ipv4-truncated":
        packet = packet[:38]
    elif kind.startswith("udp-length-"):
        packet[38:40] = struct.pack("!H", 4 if kind.endswith("short") else 512)
    elif kind == "tcp-doff4":
        packet[46] = 0x40
    return bytes(packet) + b"\0" * (max(64, (len(packet) + 3) & ~3) - len(packet))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("wan", "lan"), default="wan")
    parser.add_argument("--pc", type=int, choices=(561, 2215), default=561)
    parser.add_argument("--kind", choices=KINDS, default="ipv4-udp")
    parser.add_argument("--branch-test", action="store_true")
    parser.add_argument("--branch-swap", action="store_true")
    parser.add_argument("--rss-map-test", action="store_true")
    parser.add_argument("--hash-export", action="store_true")
    parser.add_argument("--output-hash", action="store_true")
    parser.add_argument("--ipv6-hash", action="store_true")
    parser.add_argument("--ipv6-source", type=ipaddress.IPv6Address)
    parser.add_argument("--ipv6-destination", type=ipaddress.IPv6Address)
    parser.add_argument("--metadata-loads", action="store_true")
    parser.add_argument("--native-export", action="store_true")
    parser.add_argument("--source-port", type=int, default=48000)
    parser.add_argument("--flow-hit", action="store_true")
    args = parser.parse_args()
    if args.ipv6_hash:
        if args.kind not in ("ipv6-udp", "ipv6-tcp", "ipv6-ack"):
            parser.error("IPv6 hash calibration requires basic IPv6 TCP/UDP")
        args.output_hash = args.hash_export = True
    if (args.ipv6_source or args.ipv6_destination) and not args.kind.startswith("ipv6"):
        parser.error("IPv6 address overrides require an IPv6 fixture")
    if (args.output_hash or args.metadata_loads or args.native_export) and not args.hash_export:
        parser.error("output diagnostics require --hash-export")
    if not 1 <= args.source_port <= 65535:
        parser.error("source port must fit a nonzero 16-bit value")
    if args.flow_hit and args.kind != "ipv4-udp":
        parser.error("the installed flow fixture is IPv4 UDP only")
    wire = make_frame(args.mode, args.kind, BOARD("cat /sys/class/net/eth0/address").strip(),
                      args.flow_hit, args.source_port,
                      str(args.ipv6_source) if args.ipv6_source else None,
                      str(args.ipv6_destination) if args.ipv6_destination else None)
    expected = wire if args.mode == "wan" else wire[:12] + bytes.fromhex("8100003e") + wire[12:]
    command = (["sudo", "-n", sys.executable, str(HERE / "inject-trace-frame.py"),
                "--interface", "en8"] if args.mode == "wan" else
               ["ssh", "-o", "BatchMode=yes", "cnjn@192.168.3.43", "python",
                "C:/Users/cnjn/AppData/Local/Temp/sr1010-inject-trace-frame.py",
                "--interface", r"\Device\NPF_{908BA7F3-F82E-4E1E-92C6-573623C048F9}"])
    before = STATS(BOARD("/tmp/ethtool-test -S eth0"))
    if args.hash_export:
        command += ["--seconds", "1"]
    child = subprocess.Popen(command + ["--frame", wire.hex()], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        if args.mode == "lan":
            time.sleep(0.3)
        output = BOARD(f"insmod /tmp/rx-ppu-trace.ko pc={args.pc} steps=512 " +
                       ("branch_test=1 " if args.branch_test or args.branch_swap else "") +
                       ("rss_map_test=1 " if args.rss_map_test else "") +
                       ("hash_export=1 " if args.hash_export else "") +
                       ("output_hash=1 " if args.output_hash else "") +
                       ("ipv6_hash=1 " if args.ipv6_hash else "") +
                       ("metadata_loads=1 " if args.metadata_loads else "") +
                       ("native_export=1 " if args.native_export else "") +
                       ("branch_swap=1 " if args.branch_swap else "") + "frame=" + expected.hex() +
                       "; sleep 0.1")
        sent, error = child.communicate(timeout=8)
        if child.returncode:
            raise RuntimeError(error or sent)
        after = STATS(BOARD("/tmp/ethtool-test -S eth0"))
        BOARD("mkdir -p /tmp/rss-trace; cat /sys/kernel/debug/rx-ppu-trace/trace "
              "> /tmp/rss-trace/trace.txt; httpd -f -p 8081 -h /tmp/rss-trace "
              "> /tmp/rss-http.log 2>&1 & rss_http=$!")
        trace = urllib.request.urlopen("http://192.168.1.1:8081/trace.txt", timeout=8).read().decode()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        BOARD('if [ -n "$rss_http" ]; then kill "$rss_http"; wait "$rss_http"; '
              'unset rss_http; fi; rmmod rx_ppu_trace')
    header = dict(re.findall(r"(\w+)=(-?[\da-f]+)", trace.splitlines()[0]))
    passed = all(int(header[k], 16) == 0 for k in ("result", "restored", "before", "after"))
    print(json.dumps({"mode": args.mode, "kind": args.kind,
                      "branch_test": args.branch_test or args.branch_swap,
                      "branch_swap": args.branch_swap,
                      "rss_map_test": args.rss_map_test,
                      "hash_export": args.hash_export, "output_hash": args.output_hash,
                      "ipv6_hash": args.ipv6_hash,
                      "flow_hit": args.flow_hit,
                      "frame": wire.hex(), "expected_ppu_frame": expected.hex(), "sent": int(sent),
                      "before": before, "after": after,
                      "delta": {k: after[k] - v for k, v in before.items() if k.startswith(("rx_", "tx_"))},
                      "board": output, "trace": trace, "result": "PASS" if passed else "FAIL"}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
