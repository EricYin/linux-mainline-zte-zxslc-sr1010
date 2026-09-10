#!/usr/bin/env python3
"""Independently validate bit-copy decoding, ALU controls and tuple-hash traces."""

import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re


def samples(text):
    state, rows = {}, []
    for line in text.splitlines():
        match = re.match(r"step=(\d+) pc=(\d+) inst=([0-9a-f]+)", line)
        if not match:
            continue
        state.update({int(i): int(v, 16)
                      for i, v in re.findall(r"w(\d+)=([0-9a-f]+)", line)})
        if len(state) != 128 or int(match[1]) != len(rows):
            raise ValueError("incomplete or out-of-order snapshot")
        rows.append((int(match[2]), int(match[3], 16), state.copy()))
    return rows


def audit_moves(paths):
    checks, instructions = 0, set()
    for path in paths:
        rows = samples(path.read_text())
        for (_, inst, before), (_, _, after) in zip(rows, rows[1:]):
            if inst >> 51 != 0xa0 or inst & 0x3fe0 != 0x520:
                continue
            dst, dst_bit = inst >> 46 & 31, inst >> 41 & 31
            src, src_bit = inst >> 35 & 63, inst >> 30 & 31
            width = (inst & 31) + 1
            mask = ((1 << width) - 1) << dst_bit
            old = 0 if inst & (1 << 15) else before[64 + dst]
            value = before[64 + src] >> src_bit & ((1 << width) - 1)
            expected = ((old & ~mask) | value << dst_bit) & 0xffffffff
            if after[64 + dst] != expected:
                raise ValueError(f"{path}: bit-copy mismatch {inst:016x}")
            checks += 1
            instructions.add(inst)
    return {"checks": checks, "unique_instructions": len(instructions), "result": "PASS"}


def audit_alu(path):
    checks, carry_cases = 0, 0
    for inst, a, b, observed in re.findall(
            r"alu=\d+ inst=([\da-f]+) a=([\da-f]+) b=([\da-f]+) result=([\da-f]+)",
            path.read_text()):
        inst, a, b, observed = [int(x, 16) for x in (inst, a, b, observed)]
        kind = inst >> 51 & 31
        inverted = bool(inst & (1 << 10))
        if kind == 0:
            expected = a & 0xffff0000 | (a + b) & 0xffff
        elif kind == 1:
            carry_cases += 1
            continue  # Carry input was not sampled for each operation.
        elif kind == 2:
            expected = a & 0xffff0000 | (a - b) & 0xffff
        elif kind == 4:
            expected = a ^ (b ^ (0xffffffff if inverted else 0))
        elif kind == 5:
            expected = a & (b ^ (0xffffffff if inverted else 0))
        elif kind == 6:
            expected = max(a, b)
        elif kind == 7:
            expected = min(a, b)
        elif kind == 9:
            expected = a << (b & 31)
        elif kind == 10:
            expected = a >> (b & 31)
        else:
            raise ValueError("unknown calibration form")
        if observed != expected & 0xffffffff:
            raise ValueError(f"ALU result mismatch {inst:016x}")
        checks += 1
    return {"checks": checks, "carry_dependent_cases_not_claimed": carry_cases, "result": "PASS"}


def audit_probe(probe):
    header = dict(re.findall(r"(\w+)=(-?[\da-f]+)", probe["trace"].splitlines()[0]))
    if probe["result"] != "PASS" or any(int(header[k], 16) for k in
                                      ("result", "restored", "before", "after")):
        raise ValueError("probe did not restore a completed context")
    frame = bytes.fromhex(probe["expected_ppu_frame"])
    network = 18 if frame[12:14] == b"\x81\0" else 14
    source = int(ipaddress.ip_address(frame[network + 12:network + 16]))
    destination = int(ipaddress.ip_address(frame[network + 16:network + 20]))
    ports = int.from_bytes(frame[network + 20:network + 24], "big")
    raw = source ^ destination ^ ports ^ frame[network + 9]
    folded = raw
    for shift in (16, 8, 4, 3):
        folded ^= folded >> shift
    queue = (8 if probe["mode"] == "wan" else 0) | (folded & 7)
    if (probe["raw"], probe["hash"], probe["queue"]) != (raw, folded, queue):
        raise ValueError("frame-derived hash mismatch")
    rows = samples(probe["trace"])
    if rows[0][0] != 561 or rows[-1][0] != 2217:
        raise ValueError("incomplete hash path")
    initial = rows[0][2]
    actual = b"".join(initial[i].to_bytes(4, "big") for i in range(32, 64))
    if actual[:len(frame)] != frame or (initial[14], initial[15]) != (destination, source):
        raise ValueError("hash inputs do not match selected frame")
    for _, _, state in rows[1:]:
        if state[66] != folded or any(state[i] != initial[i] for i in range(32, 64)):
            raise ValueError("hash register or frame bytes changed")
    if any(rows[1][2][i] != initial[i] for i in (67, 72)):
        raise ValueError("scratch SPR3/SPR8 were not restored")
    consumers = [i for i, (pc, _, _) in enumerate(rows) if pc in (1558, 1893)]
    if len(consumers) != 1:
        raise ValueError("missing or repeated queue consumer")
    i = consumers[0]
    if rows[i][1] != 0x0504201000000522 or rows[i + 1][2][80] != queue << 16:
        raise ValueError("hash did not feed queue selector")
    j = next(j for j, (pc, _, _) in enumerate(rows) if pc in (1560, 1895))
    if any(state[80] != (0x193 + queue) << 16 for _, _, state in rows[j + 1:]):
        raise ValueError("queue changed after final transform")
    return {"mode": probe["mode"], "source_port": probe["source_port"],
            "protocol": probe["protocol"], "variant": probe.get("variant", 0),
            "consumer_pc": rows[i][0], "queue": queue, "queue_delta": probe["queue_delta"],
            "trace_sha256": hashlib.sha256(probe["trace"].encode()).hexdigest(), "result": "PASS"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    root = args.evidence
    moves = audit_moves([root / n for n in (
        "ppu-wan-entry-561.txt", "ppu-wan-tcp-syn.txt",
        "ppu-lan-udp-tagged.txt", "ppu-lan-tcp-syn.txt")])
    probes = []
    for path in sorted((root / "hash").glob("*.json")):
        data = json.loads(path.read_text())
        probes.extend(data if isinstance(data, list) else [data])
    print(json.dumps({"moves": moves, "alu": audit_alu(root / "ppu-alu-xor.txt"),
                      "probes": [audit_probe(p) for p in probes]}, indent=2))


if __name__ == "__main__":
    main()
