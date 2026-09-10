#!/usr/bin/env python3
"""Check bulk WAN RX queue selection with bounded TCP and UDP traffic.

This measures aggregate hardware ring consumption, not RSS flow affinity.
The board must already run the queue-counter driver, ethtool-test and iperf3.
"""

import argparse
import json
from pathlib import Path
import re
import runpy
import shutil
import subprocess


HERE = Path(__file__).resolve().parent
BOARD = runpy.run_path(str(HERE / "nic-rx-accept.py"))["board"]
ERRORS = ("rx_errors", "rx_invalid_dma", "rx_page_lookup_misses")


def snapshot():
    output = BOARD("/tmp/ethtool-test -S eth0")
    values = {key: int(value) for key, value in re.findall(
        r"(?m)^\s+([a-z0-9_]+): (\d+)$", output)}
    required = (*ERRORS, "rx_refill_deficit", "rx_release_published",
                "rx_irq_count", "idm_local_irq_count",
                *(f"rx_hw_queue_{q}_descs" for q in range(16)))
    missing = set(required) - values.keys()
    if missing:
        raise RuntimeError(f"missing board counters: {sorted(missing)}")
    return {key: values[key] for key in required}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-queue", type=int, choices=range(8, 16),
                        required=True)
    parser.add_argument("--iperf3", default=shutil.which("iperf3"))
    args = parser.parse_args()
    if not args.iperf3:
        parser.error("iperf3 is required on the host")
    results = []
    for protocol in ("tcp", "udp"):
        before = snapshot()
        command = [args.iperf3, "-c", "192.168.1.1", "-P", "8",
                   "-t", "3", "-b", "2M", "-J"]
        if protocol == "udp":
            command += ["-u", "-l", "512"]
        run = subprocess.run(command, capture_output=True, text=True,
                             timeout=20, check=True)
        report = json.loads(run.stdout)
        if "error" in report:
            raise RuntimeError(report["error"])
        after = snapshot()
        delta = {key: after[key] - value for key, value in before.items()}
        queues = {q: delta[f"rx_hw_queue_{q}_descs"] for q in range(8, 16)}
        total = sum(queues.values())
        selected = queues[args.expected_queue]
        received = report["end"].get("sum_received", report["end"].get("sum"))
        passed = (selected >= 100 and selected >= total * 0.9 and
                  all(delta[key] == 0 for key in ERRORS) and
                  before["rx_refill_deficit"] == 0 and
                  after["rx_refill_deficit"] == 0 and
                  len(report["start"]["connected"]) == 8 and
                  received is not None and received["bytes"] > 0)
        if protocol == "udp":
            passed = passed and received is not None and \
                received.get("lost_packets") == 0
        results.append({"protocol": protocol, "passed": passed,
                        "expected_queue": args.expected_queue,
                        "receiver": received, "before": before,
                        "after": after, "delta": delta})
    print(json.dumps({"result": "PASS" if all(r["passed"] for r in results)
                      else "FAIL", "runs": results,
                      "limits": "Aggregate counters include background/control "
                      "traffic; this does not establish per-flow RSS affinity "
                      "or an atomic multi-counter snapshot."}, indent=2))
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
