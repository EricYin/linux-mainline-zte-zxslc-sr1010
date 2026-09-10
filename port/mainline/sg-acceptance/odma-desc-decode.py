#!/usr/bin/env python3
"""Decode ZX279133 ODMA debug words using the factory print routines.

Input is a JSON object with an 'odma' log containing
'odma PASS WORD_INDEX VALUE' lines. This tool never accesses hardware.
Debug words are not an atomic or packet-identified descriptor capture.
"""

import argparse
import json
import re
from pathlib import Path


def decode(words):
    def w(index):
        return words[index]

    def pair(high, low):
        return ((w(high) << 1) | (w(low) >> 31)) & 0xffffffff

    return {
        "counters": dict(zip(
            ["isu_in", "ppu_in", "read_smct", "out_to_red"],
            [w(i) for i in range(0x5010, 0x5014)])),
        "mf": {
            "start_pc": (w(0x5030) >> 1) & 0xffff,
            "dup_send_end_flag": w(0x5030) & 1,
            "multi_buf_num": (w(0x5033) >> 6) & 1,
            "queue_id": (w(0x5033) >> 7) & 0x1ff,
            "global_num": (w(0x5032) >> 16) & 0x3fff,
            "move_flag": (w(0x5032) >> 30) & 1,
            "destination_port": (w(0x5034) >> 2) & 0x3f,
            "packet_offset": (w(0x5034) >> 8) & 0xff,
            "third_ptr": w(0x5036) >> 18,
            "second_ptr": w(0x5038) >> 18,
            "packet_length": w(0x5039) >> 18,
            "np2_packet_length": w(0x5039) & 0x3fff,
            "source_port": w(0x503a) >> 26,
        },
        "to_tm": {
            "queue_id": (w(0x503c) >> 1) & 0x1ff,
            "packet_length": (w(0x503c) >> 10) & 0x3fff,
            "output_list": (w(0x503c) >> 25) & 0x3f,
            "move_flag": w(0x503c) >> 31,
            "np1_flag": w(0x503d) & 1,
            "first_ptr": (w(0x503d) >> 1) & 0x3fff,
            "woe_descriptor": [f"{pair(high, low):08x}" for high, low in
                               [(0x5042, 0x5041), (0x5041, 0x5040),
                                (0x504a, 0x5043), (0x5043, 0x5042),
                                (0x504c, 0x504b), (0x504b, 0x504a),
                                (0x504d, 0x504c)]],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text())
    passes = {}
    for run, index, value in re.findall(
            r"^odma (\d+) (\d+) (0x[0-9a-fA-F]+)$", capture.get("odma", ""), re.M):
        passes.setdefault(int(run), {})[int(index)] = int(value, 16)
    for stage, run, index, value in re.findall(
            r"odma ([\w-]+) pass=(\d+) word=([0-9a-f]+) value=([0-9a-f]+)",
            capture.get("odma_log", "")):
        passes.setdefault(f"{stage}/{run}", {})[int(index, 16)] = int(value, 16)
    for stage, run, values in re.findall(
            r"odma ([\w-]+) pass=(\d+) counters=([0-9a-f/]+)",
            capture.get("odma_log", "")):
        passes.setdefault(f"{stage}/{run}", {}).update(zip(
            range(0x5010, 0x5014), (int(v, 16) for v in values.split("/"))))
    if not passes:
        parser.error("no ODMA samples found")
    print(json.dumps({
        "passes": {run: decode(words) for run, words in passes.items()},
        "identical_passes": all(words == next(iter(passes.values()))
                                for words in passes.values()),
        "limits": "14-bit pointer fields are not Linux DMA addresses; stable debug "
                  "words do not identify a packet or prove atomicity or SG.",
    }, indent=2))


if __name__ == "__main__":
    main()
