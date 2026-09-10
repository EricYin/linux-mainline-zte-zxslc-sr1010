#!/usr/bin/env python3
"""Replay the three original LAN ordering tuples with an independent observer."""
import argparse
import json
from pathlib import Path
import runpy
import socket
import struct
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
BOARD = runpy.run_path(str(HERE / 'run-rss-performance.py'))['BOARD']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    start = BOARD('/tmp/udp-order-observe eth0 45000 > /tmp/order-selfcheck.txt 2>&1 '
                  '& order_selfcheck=$!; sleep 0.2; head -1 /tmp/order-selfcheck.txt')
    try:
        if 'UDP_ORDER_READY' not in start:
            raise RuntimeError(start)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(('192.168.1.100', 45000))
            for seq in (1, 3, 2, 3, 4):
                sock.sendto(struct.pack('!III', 1, 0, seq) + bytes(6), ('192.168.1.1', 5203))
                time.sleep(0.01)
    finally:
        output = BOARD('sleep 0.1; kill -TERM $order_selfcheck; wait $order_selfcheck; '
                       'unset order_selfcheck; cat /tmp/order-selfcheck.txt')
        (args.directory / 'observer-selfcheck.json').write_text(json.dumps({'output': output}, indent=2) + '\n')
    if 'backward=1 same=1 gaps=1 capture_packets=5 capture_drops=0' not in output:
        raise RuntimeError('observer did not distinguish injected gap, backward and equal sequence')
    print('observer selfcheck PASS', flush=True)
    for trial, base, mask in ((0, 46000, 0), (2, 46064, 255), (7, 46224, 255)):
        process = subprocess.run([sys.executable, str(HERE / 'run-rss-performance.py'),
                                  '--mode', 'lan', '--source-base', str(base), '--udp-pps', '100000',
                                  '--payload', '18', '--server-mask', '3', '--socket-buffer', '1048576',
                                  '--masks', str(mask), '--observe-order',
                                  '--directory', str(args.directory / f'original-{trial}')],
                                 capture_output=True, text=True, timeout=120)
        (args.directory / f'original-{trial}-process.json').write_text(json.dumps(
            {'returncode': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}, indent=2) + '\n')
        print(f'original-{trial} exit={process.returncode}', flush=True)
    for name, script, extra in (
            ('udp-final', 'run-udp-sequence.py', []),
            ('tcp-final', '../network-acceptance/nic-rx-payload-accept.py', ['--keep-affinity'])):
        process = subprocess.run([sys.executable, str(HERE / script), *extra],
                                 capture_output=True, text=True, timeout=120)
        (args.directory / (name + '.json')).write_text(process.stdout)
        if process.returncode:
            raise RuntimeError(process.stderr or process.stdout)
        print(name + ' PASS', flush=True)


if __name__ == '__main__':
    main()
