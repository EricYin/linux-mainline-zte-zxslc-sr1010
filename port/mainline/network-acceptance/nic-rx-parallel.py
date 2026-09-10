#!/usr/bin/env python3
"""Verify WAN/LAN NAPI overlap while both peers check deterministic TCP data."""

import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
BOARD = runpy.run_path(str(HERE / "nic-rx-accept.py"))["board"]


def stats(output):
    return {key: int(value) for key, value in re.findall(
        r"(?m)^\s+([a-z0-9_]+): (\d+)$", output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bytes", type=int, default=8388608)
    args = parser.parse_args()
    inventory = BOARD("cat /proc/interrupts")
    irqs = [re.search(r"(?m)^\s*(\d+):.*\s" + re.escape(name) + r"$",
                      inventory).group(1)
            for name in ("19000000.ethernet", "zx279133-idm-local")]
    paths = [f"/proc/irq/{irq}/smp_affinity" for irq in irqs]
    paths += [f"/sys/class/net/{dev}/queues/tx-{q}/xps_cpus"
              for dev in ("eth0", "lan-cpu0") for q in range(2)]
    saved = BOARD("cat " + " ".join(paths)).splitlines()
    if len(saved) != len(paths) or any(not re.fullmatch(r"[0-9a-f,]+", value)
                                       for value in saved):
        raise RuntimeError("could not read affinity masks")
    servers = []
    verified = {}
    try:
        masks = ("1", "2", "1", "2", "1", "2")
        BOARD("; ".join(f"echo {value} > {path}"
                        for path, value in zip(paths, masks)))
        before = stats(BOARD("/tmp/ethtool-test -S eth0"))
        send = []
        for mode, address in (("wan", "192.168.1.100"),
                              ("lan", "192.168.5.100")):
            for queue in range(2):
                command = (["ssh", "-o", "BatchMode=yes",
                            "cnjn@192.168.3.43", "python",
                            "C:/Users/cnjn/AppData/Local/Temp/sr1010-nic-tx-verify.py"]
                           if mode == "lan" else
                           [sys.executable, str(HERE / "nic-tx-verify.py")])
                command += ["--bind", address, "--port", str(49020 + queue),
                            "--flows", "4"]
                server = subprocess.Popen(command, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True)
                servers.append((mode, server))
                if not server.stdout.readline().startswith("READY "):
                    raise RuntimeError(f"{mode} receiver did not become ready")
                ids = " ".join(str(i) for i in range(queue * 4, queue * 4 + 4))
                send.append(f"for i in {ids}; do taskset {1 << queue} "
                            f"/tmp/nic-tx-stream {address} {49020 + queue} "
                            f'"$i" {args.bytes} > "/tmp/napi-{mode}-$i.txt" & done')
        output = BOARD("; ".join(send) + "; wait; cat /tmp/napi-wan-*.txt "
                       "/tmp/napi-lan-*.txt; /tmp/ethtool-test -S eth0")
        for mode, server in servers:
            text, error = server.communicate(timeout=25)
            if server.returncode:
                raise RuntimeError(error or text)
            verified.setdefault(mode, []).extend(json.loads(text))
        for _ in range(10):
            after = stats(BOARD("/tmp/ethtool-test -S eth0"))
            if after["tx_pending"] == 0 and all(
                    after[f"tx_queue_{q}_submitted"] ==
                    after[f"tx_queue_{q}_completed"] for q in range(2)):
                break
            time.sleep(0.05)
        delta = {key: after[key] - value for key, value in before.items()
                 if key.startswith(("rx_", "tx_"))}
        passed = len(re.findall(r"TCP_STREAM .*result=PASS", output)) == 16
        for rows in verified.values():
            passed &= (len(rows) == 8 and
                       all(row["result"] == "PASS" for row in rows) and
                       sum(row["bytes"] for row in rows) == args.bytes * 8)
        for key in ("rx_errors", "rx_invalid_dma", "rx_page_lookup_misses",
                    "rx_refill_shortfalls", "tx_errors", "tx_timeouts"):
            passed &= delta[key] == 0
        passed &= after["rx_refill_deficit"] == 0 and after["tx_pending"] == 0
        for q in range(2):
            passed &= (after[f"tx_queue_{q}_submitted"] ==
                       after[f"tx_queue_{q}_completed"])
        passed &= (delta["rx_napi_0_cpu1"] > 0 and
                   delta["rx_napi_1_cpu0"] > 0 and
                   delta["rx_napi_0_overlaps"] + delta["rx_napi_1_overlaps"] > 0 and
                   delta["rx_napi_0_foreign_pages"] +
                   delta["rx_napi_1_foreign_pages"] > 0)
        print(json.dumps({"result": "PASS" if passed else "FAIL",
                          "peers": verified, "before": before, "after": after,
                          "delta": delta,
                          "limits": "RX work includes TCP acknowledgements and "
                          "background traffic. Overlap proves concurrent poll "
                          "execution, not a throughput gain or RSS."}, indent=2))
        return 0 if passed else 1
    finally:
        for _, server in servers:
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=5)
        BOARD("; ".join(f"echo {value} > {path}"
                        for path, value in zip(paths, saved)))


if __name__ == "__main__":
    raise SystemExit(main())
