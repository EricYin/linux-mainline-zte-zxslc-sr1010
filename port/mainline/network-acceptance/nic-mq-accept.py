#!/usr/bin/env python3
"""Verify two hardware TX rings using CPU-pinned, nonzero TCP streams."""
import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys

HERE = Path(__file__).resolve().parent
CONTEXT = runpy.run_path(str(HERE / "nic-rx-accept.py"))
board, OUT = CONTEXT["board"], CONTEXT["OUT"]


def stats(text):
    return {key: int(value) for key, value in re.findall(
        r"(?m)^\s+(tx_[a-z0-9_]+): (\d+)$", text)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lan", action="store_true")
    parser.add_argument("--bytes", type=int, default=2097152)
    parser.add_argument("--queue", type=int, choices=(0, 1))
    args = parser.parse_args()
    mode = "lan" if args.lan else "wan"
    if args.queue is not None:
        mode += f"-q{args.queue}"
    device = "lan-cpu0" if args.lan else "eth0"
    address = "192.168.5.100" if args.lan else "192.168.1.100"
    OUT.mkdir(parents=True, exist_ok=True)
    masks = [1, 2] if args.queue is None else [3 if q == args.queue else 0 for q in range(2)]
    old_masks = board(f"cat /sys/class/net/{device}/queues/tx-0/xps_cpus; "
                      f"cat /sys/class/net/{device}/queues/tx-1/xps_cpus").splitlines()
    before = board(f"echo {masks[0]} > /sys/class/net/{device}/queues/tx-0/xps_cpus; "
                   f"echo {masks[1]} > /sys/class/net/{device}/queues/tx-1/xps_cpus; "
                   "/tmp/ethtool-test -S eth0")
    servers = []
    try:
        for queue in range(2):
            command = (["ssh", "cnjn@192.168.3.43", "python",
                        "C:/Users/cnjn/AppData/Local/Temp/sr1010-nic-tx-verify.py"]
                       if args.lan else [sys.executable, str(HERE / "nic-tx-verify.py")])
            command += ["--bind", address, "--port", str(49020 + queue), "--flows", "4"]
            server = subprocess.Popen(command, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            servers.append(server)
            ready = server.stdout.readline()
            if not ready.startswith("READY "):
                raise RuntimeError("receiver did not become ready: " + ready)
        command = ""
        for queue in range(2):
            ids = " ".join(str(i) for i in range(queue * 4, queue * 4 + 4))
            command += (f"for i in {ids}; do taskset {1 << queue} /tmp/nic-tx-stream "
                        f"{address} {49020 + queue} \"$i\" {args.bytes} "
                        '> "/tmp/mq-tx-$i.txt" & done; ')
        result = board(command + "wait; cat /tmp/mq-tx-*.txt; sleep 1; "
                       "/tmp/ethtool-test -S eth0")
        (OUT / f"mq-{mode}-board.txt").write_text("BEFORE\n" + before + "\nAFTER\n" + result)
        verified = []
        for queue, server in enumerate(servers):
            output, error = server.communicate(timeout=25)
            (OUT / f"mq-{mode}-q{queue}-peer.txt").write_text(output + error)
            if server.returncode:
                raise RuntimeError(error or output)
            verified += json.loads(output)
        old, new = stats(before), stats(result)
        delta = {key: new[key] - value for key, value in old.items()}
        if len(re.findall(r"TCP_STREAM .*result=PASS", result)) != 8:
            raise RuntimeError(result)
        assert len(verified) == 8 and all(row["result"] == "PASS" for row in verified)
        assert sum(row["bytes"] for row in verified) == args.bytes * 8
        for queue in range(2):
            if args.queue is None or args.queue == queue:
                assert delta[f"tx_queue_{queue}_completed"] >= 100
            assert new[f"tx_queue_{queue}_submitted"] == new[f"tx_queue_{queue}_completed"]
        assert new["tx_pending"] == 0
        assert delta["tx_errors"] == 0 and delta["tx_timeouts"] == 0
        report = {"mode": mode, "result": "PASS", "flows": 8,
                  "bytes": sum(row["bytes"] for row in verified),
                  "bad_bytes": sum(row["bad_bytes"] for row in verified),
                  "stats_delta": delta}
        (OUT / f"mq-{mode}-result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        for server in servers:
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=5)
        board(f"echo {old_masks[0]} > /sys/class/net/{device}/queues/tx-0/xps_cpus; "
              f"echo {old_masks[1]} > /sys/class/net/{device}/queues/tx-1/xps_cpus")


if __name__ == "__main__":
    main()
