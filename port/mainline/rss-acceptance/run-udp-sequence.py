#!/usr/bin/env python3
"""Collect per-flow UDP integrity and hardware-ring evidence on the RAM fixture."""

import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
NETWORK = HERE.parent / "network-acceptance"
BOARD = runpy.run_path(str(NETWORK / "nic-rx-accept.py"))["board"]
STATS = runpy.run_path(str(NETWORK / "nic-rx-parallel.py"))["stats"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("wan", "lan", "both"), default="both")
    parser.add_argument("--first", type=int, default=0)
    parser.add_argument("--flows", type=int, default=8)
    parser.add_argument("--count", type=int, default=512)
    args = parser.parse_args()
    modes = ("wan", "lan") if args.mode == "both" else (args.mode,)
    # Resolve the peer before the measured UDP burst; unlike TCP, UDP does not retry.
    for mode in modes:
        command = (["ping", "-c", "1", "-S", "192.168.1.100", "192.168.1.1"]
                   if mode == "wan" else ["ssh", "-o", "BatchMode=yes", "cnjn@192.168.3.43",
                   "ping", "-n", "1", "-w", "2000", "192.168.5.1"])
        probe = subprocess.run(command, capture_output=True, timeout=5)
        if probe.returncode or b"ttl=" not in probe.stdout.lower():
            raise RuntimeError(f"{mode} peer reachability was not established")
    clients = []
    try:
        commands = []
        for mode in modes:
            network = 1 if mode == "wan" else 5
            commands.append(f"/tmp/rss-udp-sequence 192.168.{network}.1 "
                            f"{args.first} {args.flows} {args.count} "
                            f"> /tmp/rss-udp-{mode}.txt 2>&1 & rss_udp_{mode}=$!")
        ready = BOARD("; ".join(commands) + "; sleep 0.1; " + "; ".join(
            f"head -n 1 /tmp/rss-udp-{mode}.txt" for mode in modes))
        if ready.count("READY") != len(modes):
            raise RuntimeError(ready)
        before = STATS(BOARD("/tmp/ethtool-test -S eth0"))
        for mode in modes:
            network = 1 if mode == "wan" else 5
            command = ([sys.executable, str(HERE / "udp-sequence-send.py")]
                       if mode == "wan" else ["ssh", "-o", "BatchMode=yes",
                       "cnjn@192.168.3.43", "python",
                       "C:/Users/cnjn/AppData/Local/Temp/sr1010-udp-sequence-send.py"])
            command += [f"192.168.{network}.100", f"192.168.{network}.1",
                        "--first", str(args.first), "--flows", str(args.flows),
                        "--count", str(args.count)]
            clients.append((mode, subprocess.Popen(command, stdout=subprocess.PIPE,
                                                   stderr=subprocess.PIPE, text=True)))
        sent = {}
        for mode, child in clients:
            output, error = child.communicate(timeout=15)
            if child.returncode:
                raise RuntimeError(error or output)
            sent[mode] = json.loads(output)
        output = BOARD("; ".join(f"wait $rss_udp_{mode}" for mode in modes) + "; " +
                       "; ".join(f"cat /tmp/rss-udp-{mode}.txt" for mode in modes) +
                       "; /tmp/ethtool-test -S eth0")
        after = STATS(output)
        delta = {k: after[k] - value for k, value in before.items()
                 if k.startswith(("rx_", "tx_"))}
        rows = re.findall(r"UDP_SEQUENCE .*", output)
        passed = len(rows) == len(modes) and all("result=PASS" in row for row in rows)
        passed &= all(delta[k] == 0 for k in ("rx_errors", "rx_invalid_dma",
                      "rx_page_lookup_misses", "rx_refill_shortfalls", "tx_timeouts"))
        passed &= after["rx_refill_deficit"] == 0
        print(json.dumps({"result": "PASS" if passed else "FAIL", "mode": args.mode,
                          "first": args.first, "flows": args.flows, "count": args.count,
                          "receiver": rows, "sent": sent, "before": before,
                          "after": after, "delta": delta}, indent=2))
        return 0 if passed else 1
    finally:
        for _, child in clients:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
        BOARD("; ".join(f"kill $rss_udp_{mode} 2>/dev/null; unset rss_udp_{mode}"
                        for mode in modes))


if __name__ == "__main__":
    raise SystemExit(main())
