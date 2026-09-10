#!/usr/bin/env python3
"""Audit split-worker packet ownership, queue identity and zero-copy fallback."""

import argparse
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    checks = []

    def read(name):
        return json.loads((args.directory / f"{name}.json").read_text())

    def check(name, condition):
        checks.append({"check": name, "result": "PASS" if condition else "FAIL"})

    def errors(name, data):
        check(name + " driver errors", all(data["delta"][key] == 0 for key in
              ("rx_errors", "rx_invalid_dma", "rx_page_lookup_misses", "rx_refill_shortfalls", "tx_timeouts")))
        check(name + " refill", data["after"]["rx_refill_deficit"] == 0)

    for mode in ("wan", "lan", "both"):
        name = "hashed-udp-" + mode
        data = read(name)
        modes = ("wan", "lan") if mode == "both" else (mode,)
        check(name + " sequence/bytes", len(data["receiver"]) == len(modes) and all(
              "received=4096 expected=4096 missing=0 bad=0 order=0 duplicate=0 result=PASS" in r
              for r in data["receiver"]))
        for direction in modes:
            for worker in (0, 1):
                check(name + f" {direction} worker{worker}",
                      data["delta"][f"rx_napi_{worker}_{direction}_descs"] >= 2048)
        check(name + " CPU placement", data["delta"]["rx_napi_0_cpu0"] == 0 and
              data["delta"]["rx_napi_1_cpu1"] == 0 and data["delta"]["rx_napi_0_cpu1"] > 0 and
              data["delta"]["rx_napi_1_cpu0"] > 0)
        errors(name, data)
    for name in ("baseline-tcp", "hashed-tcp"):
        data = read(name)
        check(name + " bytes", len(data["receiver_rows"]) == 16 and all(
              int(length) == 8388608 and int(bad) == 0 and result == "PASS"
              for _, length, bad, result in data["receiver_rows"]))
        errors(name, data)
    xdp = read("xdp-queues")["xdp"]
    values = {int(q): int(n) for q, n in re.findall(r"XDP_RX_QUEUE id=(\d+) packets=(\d+)", xdp)}
    check("XDP queue identities", values == {0: 2048, 1: 2048, 2: 0})
    check("XDP detach", "XDP_PASS_DETACHED" in xdp)
    for name in ("xdp-udp", "xsk-udp"):
        data = read(name)
        check(name + " sequence/bytes", len(data["receiver"]) == 2 and all(
              "received=4096 expected=4096 missing=0 bad=0 order=0 duplicate=0 result=PASS" in r
              for r in data["receiver"]))
        errors(name, data)
    data = read("xsk-udp")
    check("AF_XDP single worker", data["delta"]["rx_napi_1_polls"] == 0 and
          data["delta"]["rx_napi_0_wan_descs"] == 4096 and data["delta"]["rx_napi_0_lan_descs"] == 4096)
    wire = read("xsk-clean-wire")
    frame = bytes.fromhex(wire["wire"])
    expected = frame[6:12] + frame[:6] + frame[12:]
    check("AF_XDP exact wire echo", wire["sent"] == "10" and len(wire["frames"]) == 10 and
          all(bytes.fromhex(f) == expected for f in wire["frames"]))
    check("AF_XDP queue15 single worker", wire["delta"]["rx_hw_queue_15_descs"] == 10 and
          wire["delta"]["rx_napi_1_polls"] == 0)
    stop = read("xsk-clean-stop")["xsk"]
    check("AF_XDP completions", "rx=10 tx=10 completions=10 bytes=1120" in stop and
          "rx_dropped=0 rx_invalid=0 tx_invalid=0 rx_ring_full=0" in stop)
    check("AF_XDP reload detected", "live_read=0 mismatched_blocks=2" in stop)
    check("patch restored", "hooks/body restore=0" in stop)
    passed = all(c["result"] == "PASS" for c in checks)
    print(json.dumps({"result": "PASS" if passed else "FAIL", "checks": checks,
                      "limits": "The hash remains external and must be reapplied after UMEM "
                      "transitions. No production RSS or throughput improvement claim."}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
