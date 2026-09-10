#!/usr/bin/env python3
"""Check calibrated RX queue reads against sparse stopped-context snapshots."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


FIRMWARE_SHA256 = "056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40"
QUEUE_READ = 0x0504210680000523


def audit(path, firmware):
    text = path.read_text()
    header = dict(re.findall(r"(\w+)=(-?[\da-f]+)", text.splitlines()[0]))
    if any(int(header[k], 16) for k in ("before", "after")):
        raise ValueError(f"{path}: stopped contexts not clear")
    if int(header["result"]) or int(header["restored"]):
        raise ValueError(f"{path}: failed or incomplete trace")
    override = re.search(r"(?m)^queue=(-?\d+) replayed=(\d+)$", text)
    queue = int(override[1]) if override else -1
    state = {}
    rows = []
    replacements = []
    for line in text.splitlines():
        match = re.match(r"step=(\d+) pc=(\d+) inst=([\da-f]+)", line)
        if not match:
            continue
        step, pc = int(match[1]), int(match[2])
        instruction = int(match[3], 16)
        if step != len(rows):
            raise ValueError(f"{path}: nonconsecutive sample indices")
        original = struct.unpack_from(">Q", firmware, 16 + pc * 8)[0]
        if instruction != original:
            if (queue < 0 or pc not in (1558, 1893) or original != QUEUE_READ or
                    instruction != 0x010420000000052f | queue << 25):
                raise ValueError(f"{path}: unrecognized instruction replacement")
            replacements.append(pc)
        state.update({int(i): int(v, 16)
                      for i, v in re.findall(r"w(\d+)=([\da-f]+)", line)})
        if len(state) != 128:
            raise ValueError(f"{path}: incomplete initial register snapshot")
        rows.append({"pc": pc, "instruction": instruction, "state": state.copy()})
    if len(rows) != int(header["count"]):
        raise ValueError(f"{path}: sample count mismatch")
    indices = [i for i, row in enumerate(rows) if row["pc"] in (1558, 1893)]
    if len(indices) != 1 or len(replacements) != (queue >= 0):
        raise ValueError(f"{path}: expected exactly one queue-read operation")
    i = indices[0]
    native_queue = rows[i]["state"][96] >> 26 & 15
    effective_queue = native_queue if queue < 0 else queue
    if rows[i + 1]["state"][80] != effective_queue << 16:
        raise ValueError(f"{path}: queue-read result mismatch")
    transform_pc = 1560 if rows[i]["pc"] == 1558 else 1895
    j = next(j for j, row in enumerate(rows) if row["pc"] == transform_pc)
    final_value = (0x193 + effective_queue) << 16
    if any(row["state"][80] != final_value for row in rows[j + 1:]):
        raise ValueError(f"{path}: queue-derived SPR16 changed after transform")
    unchanged_payload = all(
        all(row["state"][w] == rows[0]["state"][w] for w in range(32, 64))
        for row in rows)
    return {"capture": str(path), "samples": len(rows),
            "capture_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "consumer_pc": rows[i]["pc"], "native_queue": native_queue,
            "effective_queue": effective_queue,
            "rsp0_at_consumer": f'{rows[i]["state"][96]:08x}',
            "spr16_after_read": f'{rows[i + 1]["state"][80]:08x}',
            "transform_pc": transform_pc, "spr16_after_transform": f"{final_value:08x}",
            "spr16_stable_to_last_sample": True,
            "packet_words_32_63_unchanged": unchanged_payload,
            "replacement_pcs": replacements, "last_sample_pc": rows[-1]["pc"],
            "result": "PASS",
            "limits": "Calibrated sampled paths only; not a general ISA decoder, "
                      "not wire-delivery or RSS hash proof. Initial registers may be stale."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("firmware", type=Path)
    parser.add_argument("captures", type=Path, nargs="+")
    args = parser.parse_args()
    firmware = args.firmware.read_bytes()
    if hashlib.sha256(firmware).hexdigest() != FIRMWARE_SHA256:
        raise ValueError("firmware differs from the calibrated image")
    print(json.dumps([audit(p, firmware) for p in args.captures], indent=2))


if __name__ == "__main__":
    main()
