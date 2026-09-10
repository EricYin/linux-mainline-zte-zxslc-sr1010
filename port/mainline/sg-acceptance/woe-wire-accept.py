#!/usr/bin/env python3
"""Observe WOE test frames on en8; print only controlled packets and board evidence."""

import argparse
import json
import os
from pathlib import Path
import runpy
import shlex
import signal
import socket
import struct
import subprocess
import threading
import time


ROOT = Path(__file__).resolve().parents[3]
MAGIC = b"ZXSG-WAV600"
CALIBRATION = b"SGWIRECAL"
PEER_MAC = bytes.fromhex("00e041680d86")


def checksum(data):
    if len(data) & 1:
        data += b"\0"
    value = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while value >> 16:
        value = (value & 0xffff) + (value >> 16)
    return (~value) & 0xffff


def expected_frame(board_mac, seed):
    payload = bytearray((i * 37 + seed) & 255 for i in range(42, 128))
    payload[:len(MAGIC)] = MAGIC
    payload[12] = seed
    src, dst = bytes([192, 168, 1, 1]), bytes([192, 168, 1, 100])
    udp = struct.pack("!HHHH", 49029, 49030, 94, 0) + payload
    udp_sum = checksum(src + dst + struct.pack("!BBH", 0, 17, len(udp)) + udp)
    udp = udp[:6] + struct.pack("!H", udp_sum or 0xffff) + udp[8:]
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 114, 0x5300 | seed,
                     0, 64, 17, 0, src, dst)
    ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
    return PEER_MAC + board_mac + b"\x08\x00" + ip + udp


def pcap_packets(raw):
    if len(raw) < 24:
        raise ValueError("Missing pcap header")
    formats = {b"\xd4\xc3\xb2\xa1": "<", b"\xa1\xb2\xc3\xd4": ">"}
    endian = formats[raw[:4]]
    if struct.unpack_from(endian + "I", raw, 20)[0] != 1:
        raise ValueError("Capture is not Ethernet")
    offset = 24
    while offset < len(raw):
        sec, usec, size, original = struct.unpack_from(endian + "4I", raw, offset)
        offset += 16
        if offset + size > len(raw):
            raise ValueError("Truncated capture record")
        yield raw[offset:offset + size], original
        offset += size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-args", required=True)
    parser.add_argument("--wifi-attrs", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    board = runpy.run_path(str(ROOT / "port/mainline/network-acceptance/nic-rx-accept.py"))["board"]
    preparation = board("dmesg -n 1; tftp -g -r woe-wav600-probe.ko "
                        "-l /tmp/woe-wav600-probe.ko 192.168.1.100")
    if args.wifi_attrs:
        preparation += board("tftp -g -r spa-wifi-attrs-ab.sh -l /tmp/spa-wifi-attrs-ab.sh "
                             "192.168.1.100; sh /tmp/spa-wifi-attrs-ab.sh apply")
    mac_text = board("cat /sys/class/net/eth0/address").strip()
    board_mac = bytes.fromhex(mac_text.replace(":", ""))
    expected = [expected_frame(board_mac, seed) for seed in (0x31, 0x62)]
    capture = subprocess.Popen(["/usr/sbin/tcpdump", "-i", "en8", "-s", "1600",
                                "-U", "-w", "-"], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    captured = {}

    def collect():
        captured["raw"], captured["stderr"] = capture.communicate()

    reader = threading.Thread(target=collect)
    reader.start()
    ping = ["/sbin/ping", "-c", "1", "-W", "1000", "-p", CALIBRATION.hex(), "192.168.1.1"]
    receiver = None
    try:
        # Keep the peer from answering the test datagram with ICMP port-unreachable.
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("192.168.1.100", 49030))
        time.sleep(0.4)
        if capture.poll() is not None:
            raise RuntimeError("tcpdump failed before the experiment")
        before = subprocess.run(ping, capture_output=True, text=True, timeout=5)
        if before.returncode:
            raise RuntimeError("WAN ping control failed before the experiment")
        module_args = " ".join(shlex.quote(v) for v in shlex.split(args.module_args))
        result = board("insmod /tmp/woe-wav600-probe.ko arm=1 wire_tx=1 " + module_args +
                       "; dmesg | grep woe-wav")
        after = subprocess.run(ping, capture_output=True, text=True, timeout=5)
        time.sleep(1.5)
    finally:
        if receiver is not None:
            receiver.close()
        if capture.poll() is None:
            capture.send_signal(signal.SIGINT)
        reader.join(timeout=5)
        if reader.is_alive():
            capture.kill()
            reader.join()
    controls = 0
    exact = [0, 0]
    fragments = [0, 0]
    candidates = []
    total = 0
    for packet, length in pcap_packets(captured["raw"]):
        total += 1
        if (len(packet) > 42 and packet[12:14] == b"\x08\x00" and
                packet[23] == 1 and packet[26:30] == bytes([192, 168, 1, 1]) and
                packet[34] == 0 and CALIBRATION in packet):
            controls += 1
        hits = [i for i, frame in enumerate(expected) if packet == frame]
        for i in hits:
            exact[i] += 1
        parts = [i for i in range(2) if packet == expected[1][i * 64:(i + 1) * 64]]
        for i in parts:
            fragments[i] += 1
        if MAGIC in packet or expected[1][64:] in packet:
            candidates.append({"length": length, "exact_frames": hits,
                               "exact_fragments": parts, "hex": packet.hex()})
    valid = controls >= 2 and after.returncode == 0
    native = "native_control=1" in shlex.split(args.module_args)
    split = "split=1" in shlex.split(args.module_args)
    poison = next((v.split("=", 1)[1] for v in shlex.split(args.module_args)
                   if v.startswith("poison_source=")), "0")
    split_submitted = "woe-wav: split rd=" in result
    if not valid:
        verdict = "INVALID_CAPTURE"
    elif poison == "2":
        gate = "source-poison np1 pc=5216 rd=1 bytes=86 result=0 retained=1" in result
        changed = result.count("source-result header-original=42 original=0 inverted=86 other=0")
        verdict = "SOURCE_READ_AFTER_RD_OBSERVED" if gate and changed == 1 else \
            "SOURCE_READ_AFTER_RD_INCONCLUSIVE"
    elif poison == "1":
        gate = "source-poison bytes=86 cpu-original=128 retained=1" in result
        verdict = "SOURCE_POST_CPU_MUTATION_PASS" if gate and exact == [1, 0] else \
            "SOURCE_POST_CPU_MUTATION_INCONCLUSIVE"
    elif not exact[0]:
        verdict = "NATIVE_CONTROL_FAILED" if native else "WOE_EGRESS_BLOCKED"
    elif native:
        verdict = "NATIVE_CONTROL_PASS"
    elif split and not split_submitted:
        verdict = "WIRE_SPLIT_NOT_RUN"
    elif split:
        if exact[1] == 1 and fragments == [0, 0]:
            verdict = "WIRE_SPLIT_FRAME_OBSERVED"
        elif exact[1] == 0 and fragments == [1, 1]:
            verdict = "WIRE_SPLIT_SEPARATE_FRAMES"
        else:
            verdict = "WIRE_SPLIT_FRAME_MISSING" if not exact[1] else "WIRE_SPLIT_UNEXPECTED_FRAMES"
    else:
        verdict = "WOE_WIRE_CONTROL_PASS"
    print(json.dumps({"verdict": verdict, "board_mac": mac_text, "args": args.module_args,
                      "wifi_attrs": args.wifi_attrs, "capture_packets": total,
                      "capture_valid": valid,
                      "split_submitted": split_submitted,
                      "calibration_replies": controls, "exact_wire_frames": exact,
                      "exact_wire_fragments": fragments,
                      "test_packets": candidates, "capture_status": captured["stderr"].decode(),
                      "preparation": preparation, "board": result}))


if __name__ == "__main__":
    main()
