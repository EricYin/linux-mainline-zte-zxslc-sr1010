#!/usr/bin/env python3
"""Verify conditional branches and guarded hash/fallback from saved packet traces."""

import argparse
import ipaddress
import json
from pathlib import Path
import re


def samples(text):
    state, result = {}, []
    for line in text.splitlines():
        match = re.match(r"step=(\d+) pc=(\d+) inst=([a-f0-9]+)", line)
        if match:
            state.update({int(k): int(v, 16) for k, v in re.findall(r"w(\d+)=([a-f0-9]+)", line)})
            result.append((int(match[2]), int(match[3], 16), state.copy()))
    return result


def tuple_hash(frame):
    offset, ethertype = 14, int.from_bytes(frame[12:14], "big")
    while ethertype in (0x8100, 0x88a8):
        ethertype = int.from_bytes(frame[offset + 2:offset + 4], "big")
        offset += 4
    if ethertype == 0x8864:
        ethertype = 0x0800 if frame[offset + 6:offset + 8] == b"\x00\x21" else 0x86dd
        offset += 8
    if ethertype != 0x0800:
        return None
    header = frame[offset:]
    if header[0] >> 4 != 4 or int.from_bytes(header[6:8], "big") & 0x3fff or header[9] not in (6, 17):
        return None
    transport = (header[0] & 15) * 4
    value = (int(ipaddress.ip_address(header[12:16])) ^ int(ipaddress.ip_address(header[16:20])) ^
             int.from_bytes(header[transport:transport + 4], "big") ^ header[9])
    for shift in (16, 8, 4, 3):
        value ^= value >> shift
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    checks, cases = [], []

    def check(name, passed):
        checks.append({"check": name, "result": "PASS" if passed else "FAIL"})

    for name, swap in (("branch-positive", 0), ("branch-swapped", 1)):
        data = json.loads((args.directory / f"{name}.json").read_text())
        count = 0
        for condition, value, observed in re.findall(
                r"branch_cond=([a-f0-9]+) spr10=([a-f0-9]+) taken=(\d+)", data["trace"]):
            condition, value = int(condition, 16), int(value, 16)
            if condition >= 0x4000:  # Rejected inversion hypothesis; no semantic claim.
                continue
            expected = 1 if condition == 0x520 else (value >> ((condition >> 5) & 31)) & 1
            check(f"{name} {condition:04x} {value:08x}", int(observed) == (expected ^ swap))
            count += 1
        check(name + " complete", count == 77 and data["result"] == "PASS")

    for path in sorted(args.directory.glob("guarded-*.json")):
        data = json.loads(path.read_text())
        if "trace" not in data:
            continue
        name = path.stem
        rows = samples(data["trace"])
        header = dict(re.findall(r"(\w+)=(-?[a-f0-9]+)", data["trace"].splitlines()[0]))
        check(name + " completion", all(int(header[k], 16) == 0
              for k in ("result", "restored", "before", "after")) and rows[-1][0] == 2217)
        check(name + " packet bytes", all(all(state[w] == rows[0][2][w] for w in range(32, 64))
              for _, _, state in rows))
        packet = bytes.fromhex(data["expected_ppu_frame"])
        check(name + " exact matched frame", all(rows[0][2][32 + i // 4] ==
              int.from_bytes(packet[i:i + 4], "big") for i in range(0, len(packet), 4)))
        expected_hash = tuple_hash(bytes.fromhex(data["frame"]))
        hooks = [i for i, (pc, _, _) in enumerate(rows) if pc in (1558, 1893)]
        body = [pc for pc, _, _ in rows if 6000 <= pc < 6120]
        if hooks:
            check(name + " single consumer", len(hooks) == 1)
            i = hooks[0]
            site, _, before = rows[i]
            after = next(state for pc, _, state in rows[i + 1:] if pc == site + 1)
            cave = 6000 if site == 1558 else 6064
            fallback = 6043 if site == 1558 else 6113
            allowed = {80} if expected_hash is None else {66, 80}
            check(name + " register preservation", all(before[w] == after[w]
                  for w in range(64, 96) if w not in allowed))
            native = before[96] >> 26 & 15
            queue = native if expected_hash is None else (native & 8) | (expected_hash & 7)
            check(name + " consumer result", after[80] == queue << 16)
            if expected_hash is None:
                check(name + " bypasses hash", fallback in body and cave + 4 not in body)
                action = "native fallback"
            else:
                check(name + " computed hash", after[66] == expected_hash and
                      cave + 4 in body and fallback not in body)
                action = "hash"
            check(name + " hardware queue", data["delta"][f"rx_hw_queue_{queue}_descs"] >= data["sent"])
        else:
            check(name + " untouched path", expected_hash is None and not body)
            queue, action = None, "native path outside hooks"
        baseline = args.directory / name.replace("guarded-", "native-", 1)
        baseline = baseline.with_suffix(".json")
        if baseline.exists():
            native_rows = samples(json.loads(baseline.read_text())["trace"])
            check(name + " native PC sequence", [pc for pc, _, _ in native_rows] ==
                  [pc for pc, _, _ in rows if not 6000 <= pc < 6120])
        check(name + " driver errors", all(data["delta"][k] == 0 for k in
              ("rx_errors", "rx_invalid_dma", "rx_page_lookup_misses", "rx_refill_shortfalls", "tx_timeouts")))
        cases.append({"mode": data["mode"], "kind": data["kind"], "action": action,
                      "queue": queue, "sent": data["sent"], "body_pcs": body})
    passed = all(row["result"] == "PASS" for row in checks)
    print(json.dumps({"result": "PASS" if passed else "FAIL", "checks": checks, "cases": cases,
                      "limits": "Sampled formats and branch predicates only. Header-relative "
                      "encapsulation traces do not prove complete interface or offload support."}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
