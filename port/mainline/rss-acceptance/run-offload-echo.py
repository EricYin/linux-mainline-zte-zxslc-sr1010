#!/usr/bin/env python3
"""Measure exact fixed-topology TC hardware-flow hits during endpoint echo."""

import json
from pathlib import Path
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
NETWORK = HERE.parent / "network-acceptance"
BOARD = runpy.run_path(str(NETWORK / "nic-rx-accept.py"))["board"]
STATS = runpy.run_path(str(NETWORK / "nic-rx-parallel.py"))["stats"]


def snapshot():
    return BOARD("/tmp/tc -s filter show dev lan-cpu0 ingress; "
                 "/tmp/tc -s filter show dev eth0 ingress; /tmp/ethtool-test -S eth0")


def main():
    children, result = [], {}
    try:
        mac = BOARD("cat /sys/class/net/eth0/address").strip()
        commands = ([sys.executable, str(HERE / "flow-echo-probe.py"), "server"],
                    ["ssh", "-o", "BatchMode=yes", "cnjn@192.168.3.43", "python",
                     "C:/Users/cnjn/AppData/Local/Temp/sr1010-flow-echo-probe.py", "client",
                     "--router-mac", mac])
        for command in commands:
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
            children.append(child)
            if child.stdout.readline().strip() != "READY":
                raise RuntimeError("endpoint did not become ready")
        before = snapshot()
        for child in children:
            child.stdin.write("GO\n")
            child.stdin.flush()
        for child in children:
            output, error = child.communicate(timeout=8)
            data = json.loads(output) if output else {"result": "FAIL", "error": error}
            result[data.get("role", "unknown")] = data
        after = snapshot()
        old, new = STATS(before), STATS(after)
        delta = {k: new[k] - v for k, v in old.items() if k.startswith(("rx_", "tx_"))}
        passed = len(result) == 2 and all(row["result"] == "PASS" for row in result.values())
        passed &= all(delta[k] == 0 for k in ("rx_errors", "rx_invalid_dma",
                      "rx_page_lookup_misses", "rx_refill_shortfalls", "tx_timeouts"))
        print(json.dumps({"result": "PASS" if passed else "FAIL", "endpoints": result,
                          "before": before, "after": after, "delta": delta}))
        return 0 if passed else 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
