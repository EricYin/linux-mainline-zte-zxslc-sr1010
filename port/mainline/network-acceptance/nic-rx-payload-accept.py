#!/usr/bin/env python3
"""Verify concurrent incoming WAN/LAN payload bytes and independent NAPI."""

import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(HERE / "nic-rx-parallel.py"))
BOARD, stats = HELPERS["BOARD"], HELPERS["stats"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-affinity", action="store_true",
                        help="test existing IRQ affinity without changing it")
    args = parser.parse_args()
    inventory = BOARD("cat /proc/interrupts")
    irqs = [re.search(r"(?m)^\s*(\d+):.*\s" + re.escape(name) + r"$",
                      inventory).group(1)
            for name in ("19000000.ethernet", "zx279133-idm-local")]
    paths = [f"/proc/irq/{irq}/smp_affinity" for irq in irqs]
    saved = BOARD("cat " + " ".join(paths)).splitlines()
    if len(saved) != 2 or any(not re.fullmatch(r"[0-9a-f,]+", v) for v in saved):
        raise RuntimeError("could not read IRQ affinities")
    clients = []
    try:
        if not args.keep_affinity:
            BOARD(f"echo 1 > {paths[0]}; echo 2 > {paths[1]}")
        ready = BOARD("/tmp/nic-rx-stream 192.168.1.1 49300 "
                      "> /tmp/napi-in-wan.txt 2>&1 & napi_in_wan=$!; "
                      "/tmp/nic-rx-stream 192.168.5.1 49300 "
                      "> /tmp/napi-in-lan.txt 2>&1 & napi_in_lan=$!; "
                      "sleep 0.2; head -n 1 /tmp/napi-in-wan.txt; "
                      "head -n 1 /tmp/napi-in-lan.txt")
        if "READY 192.168.1.1" not in ready or "READY 192.168.5.1" not in ready:
            raise RuntimeError(ready)
        for mode, command in (
                ("wan", [sys.executable, str(HERE / "nic-rx-send.py"),
                         "192.168.1.1", "--wait"]),
                ("lan", ["ssh", "-o", "BatchMode=yes", "cnjn@192.168.3.43",
                         "python", "C:/Users/cnjn/AppData/Local/Temp/sr1010-nic-rx-send.py",
                         "192.168.5.1", "--wait"])):
            child = subprocess.Popen(command, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True)
            clients.append((mode, child))
            if child.stdout.readline().strip() != "READY":
                raise RuntimeError(f"{mode} sender did not become ready")
        before = stats(BOARD("/tmp/ethtool-test -S eth0"))
        for _, child in clients:
            child.stdin.write("GO\n")
            child.stdin.flush()
        senders = {}
        for mode, child in clients:
            output, error = child.communicate(timeout=25)
            if child.returncode:
                raise RuntimeError(error or output)
            senders[mode] = json.loads(output)
        output = BOARD("wait $napi_in_wan; wait $napi_in_lan; "
                       "cat /tmp/napi-in-wan.txt /tmp/napi-in-lan.txt; "
                       "/tmp/ethtool-test -S eth0")
        after = stats(output)
        delta = {k: after[k] - v for k, v in before.items()
                 if k.startswith(("rx_", "tx_"))}
        rows = re.findall(r"RX_STREAM id=(\d+) bytes=(\d+) bad=(\d+) result=(\w+)",
                          output)
        lengths = sorted(int(n) for _, n, _, _ in rows)
        passed = lengths == [8388608] * 16
        passed &= all(int(b) == 0 and result == "PASS" for _, _, b, result in rows)
        passed &= all(len(v) == 8 and all(r["result"] == "PASS" for r in v)
                      for v in senders.values())
        passed &= all(delta[k] == 0 for k in ("rx_errors", "rx_invalid_dma",
                      "rx_page_lookup_misses", "rx_refill_shortfalls", "tx_timeouts"))
        passed &= after["rx_refill_deficit"] == 0
        passed &= (delta["rx_napi_0_cpu1"] > 0 and delta["rx_napi_1_cpu0"] > 0 and
                   delta["rx_napi_0_overlaps"] + delta["rx_napi_1_overlaps"] > 0)
        print(json.dumps({"result": "PASS" if passed else "FAIL", "senders": senders,
                          "receiver_rows": rows, "before": before, "after": after,
                          "delta": delta,
                          "initial_irq_affinity": saved,
                          "affinity_unchanged": args.keep_affinity,
                          "limits": "Byte integrity and overlapping NAPI "
                          "execution are checked; throughput gain and RSS "
                          "flow affinity are not claimed."}, indent=2))
        return 0 if passed else 1
    finally:
        for _, child in clients:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
        cleanup = "kill $napi_in_wan $napi_in_lan 2>/dev/null"
        if not args.keep_affinity:
            cleanup += "; " + "; ".join(
                f"echo {v} > {p}" for p, v in zip(paths, saved))
        BOARD(cleanup)


if __name__ == "__main__":
    raise SystemExit(main())
