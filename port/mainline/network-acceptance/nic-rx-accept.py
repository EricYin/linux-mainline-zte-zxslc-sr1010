#!/usr/bin/env python3
"""Run bounded checksum wire tests through the existing SR1010 serial console."""
import argparse
import fcntl
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
PANE = "sr1010-serial:0"
OUT = ROOT / "out/acceptance/20260905-nic-offloads"


def board(command):
    # All users of this shared helper address the same serial shell.
    with open('/tmp/zx279133-serial.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _board_locked(command)


def _board_locked(command):
    token = uuid.uuid4().hex[:10]
    start, end = "NIC_START_" + token, "NIC_END_" + token
    line = f"printf '\\n{start}\\n'; {command}; printf '\\n{end}\\n'"
    buffer = "nic-" + token
    # Pacing avoids PL011 input overruns when the console prints during a paste.
    for offset in range(0, len(line), 32):
        # Buffer input also preserves trailing semicolons, which tmux's
        # command-line parser otherwise treats as command separators.
        subprocess.run(["tmux", "load-buffer", "-b", buffer, "-"],
                       input=line[offset:offset + 32], text=True, check=True)
        subprocess.run(["tmux", "paste-buffer", "-b", buffer,
                        "-t", PANE, "-d"], check=True)
        time.sleep(0.01)
    subprocess.run(["tmux", "send-keys", "-t", PANE, "Enter"], check=True)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        lines = subprocess.check_output(
            ["tmux", "capture-pane", "-pJt", PANE, "-S", "-700"], text=True).splitlines()
        if start in lines and end in lines:
            return "\n".join(lines[lines.index(start) + 1:lines.index(end)])
        time.sleep(0.1)
    raise TimeoutError(command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["on", "off", "extended", "lan", "xdp"])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    interface = "lan1" if args.mode == "lan" else "eth0"
    conduit = "lan-cpu0" if args.mode == "lan" else "eth0"
    enabled = "off" if args.mode == "off" else "on"
    result = board(f"/tmp/ethtool-test -K {conduit} gro off; "
                   f"/tmp/ethtool-test -K {interface} gro off; "
                   f"/tmp/ethtool-test -K {conduit} rx {enabled}; "
                   f"cat /sys/class/net/{interface}/address")
    if "Cannot" in result or "Could not" in result or "not supported" in result:
        raise RuntimeError(result)
    mac = re.findall(r"(?m)^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$", result)[-1]
    if args.mode == "xdp":
        result = board("/tmp/nic-xdp-pass eth0 > /tmp/nic-xdp.txt 2>&1 & "
                       "nic_xdp=$!; sleep 0.2; cat /tmp/nic-xdp.txt")
        if "XDP_PASS_READY" not in result:
            raise RuntimeError(result)
    remote_log = "/tmp/nic-rx-" + args.mode + "-final.txt"
    ready = board(f"/tmp/nic-rx-observe {interface} 120 > {remote_log} & "
                  "nic_obs=$!; sleep 0.2; " + f"head -n 1 {remote_log}")
    if "READY " + interface not in ready:
        raise RuntimeError(ready)
    formats = ["plain"] if args.mode != "extended" else [
        "vlan", "qinq", "options", "fragment", "pppoe"]
    sent = []
    try:
        for index, fmt in enumerate(formats):
            for version in (4, 6):
                network = 5 if args.mode == "lan" else 1
                source = f"192.168.{network}.100" if version == 4 else f"fd00:{network}::100"
                dest = f"192.168.{network}.1" if version == 4 else f"fd00:{network}::1"
                params = ["--dst-mac", mac, "--src", source, "--dst", dest,
                          "--format", fmt, "--port-base", str(48000 + index * 100),
                          "--repeat", "3" if fmt == "plain" else "1"]
                if args.mode == "lan":
                    params += ["--src-mac", "f8:89:3c:26:fe:02", "--interface",
                               "\\Device\\NPF_{908BA7F3-F82E-4E1E-92C6-573623C048F9}"]
                    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                           "cnjn@192.168.3.43",
                           "python C:/Users/cnjn/AppData/Local/Temp/sr1010-nic-checksum-packets.py "
                           + " ".join(params)]
                else:
                    cmd = ["sudo", "-n", "python3", str(Path(__file__).with_name(
                        "nic-checksum-packets.py")), *params]
                run = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=15)
                sent += [json.loads(line) | {"ip": version} for line in run.stdout.splitlines()]
    finally:
        received = board(f"kill -INT $nic_obs; wait $nic_obs; cat {remote_log}")
        if args.mode == "xdp":
            received += "\n" + board("kill -INT $nic_xdp; wait $nic_xdp; cat /tmp/nic-xdp.txt")
        (OUT / ("rx-" + args.mode + ".txt")).write_text(received + "\n")
        (OUT / ("rx-" + args.mode + "-sent.json")).write_text(json.dumps(sent, indent=2) + "\n")
    rows = [dict(zip(["ip", "proto", "port", "bytes", "csum_valid", "status"], match))
            for match in re.findall(r"RX ip=(\d+) proto=(\d+) port=(\d+) bytes=(\d+) "
                                    r"csum_valid=(\d+) status=(0x[0-9a-f]+)", received)]
    actual = Counter((int(row["ip"]), int(row["port"]), int(row["csum_valid"]))
                     for row in rows)
    if args.mode != "extended":
        checked = 0 if args.mode in ("off", "xdp") else 1
        expected = Counter({(4, 48000, checked): 3, (4, 48002, checked): 3,
                            (4, 48004, 0): 3, (6, 48000, checked): 3,
                            (6, 48002, checked): 3})
        if actual != expected:
            raise AssertionError(f"packet validation mismatch: actual={actual}, expected={expected}")
    else:
        for index in range(len(formats)):
            for ip, case in [(4, 0), (4, 2), (4, 4), (6, 0), (6, 2)]:
                checked = int(index == 0 and case != 4)
                if actual[(ip, 48000 + index * 100 + case, checked)] != 1:
                    raise AssertionError(f"missing good packet: format={formats[index]}, ip={ip}, case={case}")
        if any(checked and (port >= 48100 or port % 100 not in (0, 2))
               for ip, port, checked in actual):
            raise AssertionError(f"unsupported packet marked valid: {actual}")
    print(json.dumps({"mode": args.mode, "sent_cases": len(sent), "received": rows,
                      "result": "PASS",
                      "log": str(OUT / ("rx-" + args.mode + ".txt"))}, indent=2))


if __name__ == "__main__":
    main()
