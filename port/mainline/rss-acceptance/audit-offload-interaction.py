#!/usr/bin/env python3
"""Audit flow-hit coexistence and explicitly bounded malformed-frame observations."""

import argparse
import json
from pathlib import Path
import re


def hardware(text):
    blocks = re.split(r"(?m)^filter protocol ip pref 38101 flower chain 0 handle \S+", text)[1:]
    values = []
    for block in blocks:
        pairs = set((int(a), int(b)) for a, b in re.findall(r"Sent hardware (\d+) bytes (\d+) pkt", block))
        if not pairs and "in_hw_count 1" in block and "Sent 0 bytes 0 pkt" in block:
            pairs = {(0, 0)}
        if len(pairs) != 1:
            raise ValueError("action copies do not agree on one hardware-flow count")
        values.append(next(iter(pairs)))
    if len(values) != 2:
        raise ValueError("expected two hardware directions")
    return values


def header(data):
    return dict(re.findall(r"(\w+)=(-?[a-f0-9]+)", data["trace"].splitlines()[0]))


def pcs(data):
    return [int(v) for v in re.findall(r"(?m)^step=\d+ pc=(\d+)", data["trace"])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    checks, echoes, malformed = [], [], []

    def read(name):
        return json.loads((args.directory / f"{name}.json").read_text())

    def check(name, passed):
        checks.append({"check": name, "result": "PASS" if passed else "FAIL"})

    for name in ("native-echo", "guarded-echo", "after-echo"):
        data = read(name)
        before, after = hardware(data["before"]), hardware(data["after"])
        delta = [(a[0] - b[0], a[1] - b[1]) for b, a in zip(before, after)]
        check(name + " hardware hits", delta == [(39168, 128), (38656, 128)])
        check(name + " endpoint bytes", set(data["endpoints"]) == {"server", "client"} and
              all(r["packets"] == 128 and r["bytes"] == 32768 and r["bad"] == 0 and
                  not r["error"] and r["result"] == "PASS" for r in data["endpoints"].values()))
        check(name + " no CPU RX descriptors", all(data["delta"][f"rx_hw_queue_{q}_descs"] == 0
              for q in range(16)))
        check(name + " no driver errors", all(data["delta"][k] == 0 for k in
              ("rx_errors", "rx_invalid_dma", "rx_page_lookup_misses", "rx_refill_shortfalls", "tx_timeouts")))
        echoes.append({"phase": name, "hardware_delta": delta})

    for mode in ("wan", "lan"):
        data = read(f"flow-hit-{mode}-ipv4-udp")
        h, path = header(data), pcs(data)
        check(mode + " hardware path completes", all(int(h[k], 16) == 0 for k in
              ("result", "restored", "before", "after")) and path[-1] == 937)
        check(mode + " bypasses hash hooks", all(p not in (1558, 1893) and not 6000 <= p < 6120 for p in path))

    for path in sorted(args.directory.glob("guarded-wan-*.json")):
        guarded = json.loads(path.read_text())
        native = read(path.stem.replace("guarded-", "native-", 1))
        gh, nh = header(guarded), header(native)
        kind = guarded["kind"]
        check(kind + " exact A/B frame", guarded["frame"] == native["frame"])
        check(kind + " context restoration", all(int(h[k], 16) == 0 for h in (gh, nh)
              for k in ("restored", "before", "after")))
        if kind == "ipv4-truncated":
            original, patched = pcs(native), pcs(guarded)
            check(kind + " native completion", gh["result"] == nh["result"] == "0" and
                  original == [p for p in patched if not 6000 <= p < 6120])
            check(kind + " hash observation", 6004 in patched and 1558 in original)
            observation = "Matched in both phases; parsed tuple is hashed despite truncated payload."
        else:
            check(kind + " unchanged unmatched observation", gh["result"] == nh["result"] == "-61" and
                  gh["count"] == nh["count"] == "0")
            observation = "Unmatched in both phases; drop location and alternate paths remain unknown."
        malformed.append({"kind": kind, "observation": observation})
    check("eight malformed comparisons", len(malformed) == 8)
    cycle = read("cycle")
    check("cycle live instruction readback", "live_read=0 mismatched_blocks=0" in cycle["state"] and
          "hooked=2 restore_result=0 stopped=00000000" in cycle["state"])
    udp, tcp = read("cycle-udp"), read("cycle-tcp")
    check("cycle UDP sequence integrity", len(udp["receiver"]) == 2 and all(
          "received=4096 expected=4096 missing=0 bad=0 order=0 duplicate=0 result=PASS" in row
          for row in udp["receiver"]))
    check("cycle UDP ring distribution", all(udp["delta"][f"rx_hw_queue_{q}_descs"] == 512 for q in range(16)))
    check("cycle TCP byte integrity", len(tcp["receiver_rows"]) == 16 and all(
          int(length) == 8388608 and int(bad) == 0 and result == "PASS"
          for _, length, bad, result in tcp["receiver_rows"]))
    for name, data in (("cycle UDP", udp), ("cycle TCP", tcp)):
        check(name + " driver errors", all(data["delta"][k] == 0 for k in
              ("rx_errors", "rx_invalid_dma", "rx_page_lookup_misses", "rx_refill_shortfalls", "tx_timeouts")))
    check("final instruction restoration", "hooks/body restore=0" in read("final-unload")["restore"])
    passed = all(c["result"] == "PASS" for c in checks)
    print(json.dumps({"result": "PASS" if passed else "FAIL", "checks": checks,
                      "echoes": echoes, "malformed": malformed,
                      "limits": "PASS describes coexistence and matched A/B observations, "
                      "not universal malformed-packet rejection or production RSS."}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
