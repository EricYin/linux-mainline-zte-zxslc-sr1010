#!/usr/bin/env python3
"""Send a bounded stream of exact trace fixtures via libpcap/Npcap."""

import argparse
import ctypes
import ctypes.util
import os
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True)
    parser.add_argument("--frame", required=True)
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument("--count", type=int)
    args = parser.parse_args()
    if not 0 < args.seconds <= 10:
        parser.error("duration must be between 0 and 10 seconds")
    if args.count is not None and not 1 <= args.count <= 10000:
        parser.error("count must be between 1 and 10000")
    frame = bytes.fromhex(args.frame)
    if not 64 <= len(frame) <= 128 or len(frame) % 4:
        parser.error("trace frame must be 64..128 bytes, a multiple of four")
    if os.name == "nt":
        folder = os.path.join(os.environ["SystemRoot"], "System32", "Npcap")
        dll_dir = os.add_dll_directory(folder)
        pcap = ctypes.CDLL(os.path.join(folder, "wpcap.dll"))
    else:
        pcap = ctypes.CDLL(ctypes.util.find_library("pcap"))
    pcap.pcap_open_live.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_char_p]
    pcap.pcap_open_live.restype = ctypes.c_void_p
    pcap.pcap_sendpacket.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    pcap.pcap_close.argtypes = [ctypes.c_void_p]
    error = ctypes.create_string_buffer(256)
    handle = pcap.pcap_open_live(args.interface.encode(), 65535, 0, 100, error)
    if not handle:
        raise RuntimeError(error.value.decode())
    count = 0
    try:
        deadline = time.monotonic() + args.seconds
        while (count < args.count if args.count is not None else time.monotonic() < deadline):
            if pcap.pcap_sendpacket(handle, frame, len(frame)):
                raise RuntimeError("pcap_sendpacket failed")
            count += 1
            time.sleep(0.001)
    finally:
        pcap.pcap_close(handle)
        if os.name == "nt":
            dll_dir.close()
    print(count)


if __name__ == "__main__":
    main()
