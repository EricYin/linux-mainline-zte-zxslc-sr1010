#!/usr/bin/env python3
"""Independently check saved continuous PPU hash acceptance evidence."""

import argparse
import ipaddress
import json
from pathlib import Path
import re


ERRORS = ("rx_errors", "rx_invalid_dma", "rx_page_lookup_misses",
          "rx_refill_shortfalls", "tx_timeouts")


def predicted(source, destination, sport, dport, protocol):
    value = (int(ipaddress.ip_address(source)) ^ int(ipaddress.ip_address(destination)) ^
             (sport << 16 | dport) ^ protocol)
    for shift in (16, 8, 4, 3):
        value ^= value >> shift
    return value & 7


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    checks = []

    def read(name):
        return json.loads((args.directory / f"continuous-{name}.json").read_text())

    def check(name, condition):
        checks.append({"check": name, "result": "PASS" if condition else "FAIL"})

    def errors(name, row):
        check(name + " errors", all(row["delta"][key] == 0 for key in ERRORS) and
              row["after"]["rx_refill_deficit"] == 0)

    def queues(row, expected):
        return all(row["delta"][f"rx_hw_queue_{q}_descs"] == expected.get(q, 0)
                   for q in range(16))

    def receiver(name, row):
        expected = row["flows"] * row["count"]
        match = (f"received={expected} expected={expected} missing=0 bad=0 "
                 "order=0 duplicate=0 result=PASS")
        check(name + " bytes/order", len(row["receiver"]) == 2 and
              all(match in line for line in row["receiver"]))
        errors(name, row)

    for name in ("baseline", "after-unload"):
        row = read(name)
        receiver(name, row)
        check(name + " native queues", queues(row, {0: row["count"], 8: row["count"]}))

    matrix = read("udp-matrix")
    check("eight distinct source flows", sorted(row["first"] for row in matrix) == list(range(8)))
    selected = []
    for row in matrix:
        name = f"UDP source {48000 + row['first']}"
        receiver(name, row)
        low = predicted("192.168.1.100", "192.168.1.1", 48000 + row["first"], 49000, 17)
        selected.append(low)
        check(name + " queues", queues(row, {low: row["count"], low + 8: row["count"]}))
    check("all eight queue indices", sorted(selected) == list(range(8)))
    row = read("udp-concurrent")
    receiver("concurrent UDP", row)
    check("concurrent sixteen queues", queues(row, dict.fromkeys(range(16), row["count"])))

    rows = read("syn-ack")
    check("both ports SYN and ACK", sorted((r["mode"], r["ack"]) for r in rows) ==
          [("lan", False), ("lan", True), ("wan", False), ("wan", True)])
    for row in rows:
        name = f"{row['mode']} TCP {'ACK' if row['ack'] else 'SYN'}"
        frame = bytes.fromhex(row["frame"])
        source, destination = str(ipaddress.ip_address(frame[26:30])), str(ipaddress.ip_address(frame[30:34]))
        low = predicted(source, destination, int.from_bytes(frame[34:36], "big"),
                        int.from_bytes(frame[36:38], "big"), frame[23])
        queue = low + (8 if row["mode"] == "wan" else 0)
        check(name + " queue", row["queue"] == queue and
              row["delta"][f"rx_hw_queue_{queue}_descs"] == int(row["sent"]))
        errors(name, row)

    row = read("tcp")
    check("TCP 128 MiB byte integrity", len(row["receiver_rows"]) == 16 and
          all(int(length) == 8388608 and int(bad) == 0 and result == "PASS"
              for _, length, bad, result in row["receiver_rows"]))
    errors("TCP", row)
    row = read("restore")
    check("no stopped contexts", "hooked=2 restore_result=0 stopped=00000000" in row["status"])
    check("hook and body restoration", "hooks/body restore=0" in row["restore"])
    words = re.findall(r"site=(\d+) cave=(\d+) words=(\d+)", row["status"])
    check("bounded code regions", words == [("1558", "6000", "40"), ("1893", "6064", "48")])
    passed = all(c["result"] == "PASS" for c in checks)
    print(json.dumps({"result": "PASS" if passed else "FAIL", "checks": checks,
                      "limits": "Controlled IPv4 diagnostic only; no production RSS, "
                      "format fallback, throughput or per-interface CPU scaling claim."}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
