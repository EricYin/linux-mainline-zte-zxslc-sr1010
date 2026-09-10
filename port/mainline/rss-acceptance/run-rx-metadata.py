#!/usr/bin/env python3
"""Correlate bounded UDP reception with stable consumed RX descriptor slots."""
import argparse
import json
from pathlib import Path
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(HERE.parent / 'network-acceptance/nic-rx-parallel.py'))
BOARD, STATS = HELPERS['BOARD'], HELPERS['stats']
QUEUE = runpy.run_path(str(HERE / 'audit-rss-map.py'))['queue']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flow', type=int, choices=range(8), required=True)
    args = parser.parse_args()
    # Counts map directly to consumer indices only without an intervening reload.
    initial = STATS(BOARD('/tmp/ethtool-test -S eth0'))
    if initial['rx_hash_loads'] != 1 or initial['rx_hash_active'] != 1:
        raise RuntimeError('requires a fresh default-on RAM boot')
    child = subprocess.run([sys.executable, str(HERE / 'run-udp-sequence.py'),
                            '--first', str(args.flow), '--flows', '1', '--count', '16'],
                           capture_output=True, text=True, check=True)
    traffic = json.loads(child.stdout)
    captures = {}
    for port in ('wan', 'lan'):
        queue = QUEUE(port, 48000 + args.flow, 17, 49000, 0xf0)
        key = f'rx_hw_queue_{queue}_descs'
        count = traffic['delta'][key]
        if not 16 <= count <= 64:
            raise RuntimeError('unbounded background traffic in selected ring')
        start = traffic['before'][key] & 2047
        command = f'/tmp/rx-desc-read {queue} {start} {count}'
        first, second = json.loads(BOARD(command)), json.loads(BOARD(command))
        captures[port] = {'first': first, 'second': second, 'stable': first == second}
    final = STATS(BOARD('/tmp/ethtool-test -S eth0'))
    if final['rx_hash_loads'] != 1:
        raise RuntimeError('engine reloaded during capture')
    print(json.dumps({'flow': args.flow, 'traffic': traffic, 'captures': captures,
                      'final': final}, indent=2))


if __name__ == '__main__':
    main()
