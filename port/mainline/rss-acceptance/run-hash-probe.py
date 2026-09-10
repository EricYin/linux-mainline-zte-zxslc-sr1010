#!/usr/bin/env python3
"""Run bounded, single-context IPv4 tuple-hash probes; emit evidence as JSON."""

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
FRAME = runpy.run_path(str(NETWORK / "nic-checksum-packets.py"))["frame"]
CHECKSUM = runpy.run_path(str(NETWORK / "nic-checksum-packets.py"))["checksum"]
STATS = runpy.run_path(str(NETWORK / "nic-rx-parallel.py"))["stats"]


def probe(mode, port, proto, variant=0, late=False):
    mac = BOARD("cat /sys/class/net/eth0/address").strip()
    network = 1 if mode == "wan" else 5
    source, destination = f"192.168.{network}.100", f"192.168.{network}.1"
    src_mac = "00:e0:41:68:0d:86" if mode == "wan" else "f8:89:3c:26:fe:02"
    wire = FRAME(src_mac, mac, source, destination, proto, port) + b"\0\0"
    if variant:
        packet = bytearray(wire)
        packet[18] ^= 0x55  # IPv4 ID is not a tuple input.
        packet[-3] ^= 0x5a  # Change the final payload byte, not Ethernet padding.
        if variant == 2:
            packet[47] = 0x10  # Same TCP flow, ACK instead of SYN.
            packet[41] ^= 0x55  # Change TCP sequence number.
        packet[24:26] = b"\0\0"
        packet[24:26] = struct.pack("!H", CHECKSUM(packet[14:34]))
        offset = 40 if proto == 17 else 50
        packet[offset:offset + 2] = b"\0\0"
        pseudo = packet[26:34] + struct.pack("!BBH", 0, proto, len(packet) - 36)
        checksum = CHECKSUM(pseudo + packet[34:-2])
        packet[offset:offset + 2] = struct.pack("!H", checksum or (0xffff if proto == 17 else 0))
        wire = bytes(packet)
    expected = wire if mode == "wan" else wire[:12] + bytes.fromhex("8100003e") + wire[12:]
    raw = (int(ipaddress.ip_address(source)) ^ int(ipaddress.ip_address(destination)) ^
           (port << 16 | 49000) ^ proto)
    folded = raw
    for shift in (16, 8, 4, 3):
        folded ^= folded >> shift
    queue = (8 if mode == "wan" else 0) | (folded & 7)
    command = (["sudo", "-n", sys.executable, str(HERE / "inject-trace-frame.py"),
                "--interface", "en8"] if mode == "wan" else
               ["ssh", "-o", "BatchMode=yes", "cnjn@192.168.3.43", "python",
                "C:/Users/cnjn/AppData/Local/Temp/sr1010-inject-trace-frame.py",
                "--interface", r"\Device\NPF_{908BA7F3-F82E-4E1E-92C6-573623C048F9}"])
    before = STATS(BOARD("/tmp/ethtool-test -S eth0"))
    child = subprocess.Popen(command + ["--frame", wire.hex()], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        if mode == "lan":
            time.sleep(0.3)
        output = BOARD("insmod /tmp/rx-ppu-trace.ko pc=561 steps=512 hash_test=1 " +
                       ("late_hash=1 " if late else "") + "frame=" +
                       expected.hex() + "; head -n 4 /sys/kernel/debug/rx-ppu-trace/trace")
        sent, error = child.communicate(timeout=7)
        if child.returncode:
            raise RuntimeError(error or sent)
        # Bracket ring counters before HTTP export generates its own TCP SYN.
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
    match = re.search(r"hash_test=1 inserted=1 raw=([\da-f]+) hash=([\da-f]+) "
                      r"queue=(\d+) ops=(\d+)", trace)
    passed = (all(int(header[k], 16) == 0 for k in ("result", "restored", "before", "after"))
              and match is not None)
    passed &= bool(match and (int(match[1], 16), int(match[2], 16), int(match[3])) ==
                   (raw, folded, queue))
    passed &= bool(match and int(match[4]) in ((17, 20) if late else (19,)))
    delta = {k: after[k] - v for k, v in before.items() if k.startswith(("rx_", "tx_"))}
    passed &= all(delta[k] == 0 for k in ("rx_errors", "rx_invalid_dma",
                   "rx_page_lookup_misses", "rx_refill_shortfalls", "tx_timeouts"))
    hits = delta[f"rx_hw_queue_{queue}_descs"]
    passed &= hits >= 1
    return {"mode": mode, "source_port": port, "protocol": proto, "variant": variant,
            "late": late, "raw": raw,
            "hash": folded, "queue": queue, "queue_delta": hits,
            "sent": int(sent.strip()), "before": before, "after": after, "delta": delta,
            "frame": wire.hex(), "expected_ppu_frame": expected.hex(), "board": output,
            "trace": trace, "result": "PASS" if passed else "FAIL",
            "limits": "One selected context computes the hash. Ring deltas include "
                      "native fixture packets and background traffic; no per-packet "
                      "Linux queue/hash association or continuous RSS acceptance."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["wan", "lan"], required=True)
    parser.add_argument("--ports", default="48000")
    parser.add_argument("--proto", type=int, choices=[6, 17], default=17)
    parser.add_argument("--variant", type=int, choices=[0, 1, 2], default=0)
    parser.add_argument("--late", action="store_true")
    args = parser.parse_args()
    if args.variant == 2 and args.proto != 6:
        parser.error("variant 2 requires TCP")
    results = [probe(args.mode, int(port), args.proto, args.variant, args.late)
               for port in args.ports.split(",")]
    print(json.dumps(results))
    return 0 if all(r["result"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
