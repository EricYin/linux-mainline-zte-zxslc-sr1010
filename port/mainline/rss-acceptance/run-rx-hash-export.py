#!/usr/bin/env python3
"""Correlate one PPU hash-export replay with DMA slots and optional raw bytes."""
import argparse
import json
from pathlib import Path
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(HERE.parent / 'network-acceptance/nic-rx-parallel.py'))
BOARD, STATS = HELPERS['BOARD'], HELPERS['stats']
FORMATS = runpy.run_path(str(HERE / 'run-format-probe.py'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('wan', 'lan'), default='wan')
    parser.add_argument('--kind', choices=FORMATS['KINDS'], default='ipv4-udp')
    parser.add_argument('--source-port', type=int, default=48000)
    parser.add_argument('--raw', action='store_true')
    parser.add_argument('--output-hash', action='store_true')
    parser.add_argument('--metadata-loads', action='store_true')
    parser.add_argument('--native-export', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.source_port <= 65535:
        parser.error('source port must fit a nonzero 16-bit value')
    if args.raw and args.kind not in ('ipv4-udp', 'ipv4-tcp', 'ipv4-ack'):
        parser.error('raw verifier accepts basic IPv4 fixtures only')
    initial = STATS(BOARD('/tmp/ethtool-test -S eth0'))
    if initial['rx_hash_loads'] != 1 or initial['rx_hash_active'] != 1:
        raise RuntimeError('requires a fresh default-on RAM boot')
    raw = None
    if args.raw:
        frame = FORMATS['make_frame'](args.mode, args.kind,
                                     BOARD('cat /sys/class/net/eth0/address').strip(),
                                     False, args.source_port)
        dev = 'eth0' if args.mode == 'wan' else 'lan1'
        ready = BOARD(f'/tmp/rx-raw-verify {dev} {frame.hex()} > /tmp/rss-raw.txt 2>&1 '
                      '& rss_raw=$!; sleep 0.1; cat /tmp/rss-raw.txt')
        if 'RAW_VERIFY_READY' not in ready:
            raise RuntimeError(ready)
    try:
        result = subprocess.run([sys.executable, str(HERE / 'run-format-probe.py'),
                                 '--hash-export', '--mode', args.mode, '--kind', args.kind,
                                 '--source-port', str(args.source_port)] +
                                (['--output-hash'] if args.output_hash else []) +
                                (['--metadata-loads'] if args.metadata_loads else []) +
                                (['--native-export'] if args.native_export else []),
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        probe = json.loads(result.stdout)
    finally:
        if args.raw:
            raw = BOARD('sleep 0.2; kill -TERM $rss_raw; wait $rss_raw; '
                        'cat /tmp/rss-raw.txt; unset rss_raw')
    final = STATS(BOARD('/tmp/ethtool-test -S eth0'))
    if final['rx_hash_loads'] != 1:
        raise RuntimeError('engine reloaded during probe')
    descriptors = []
    for queue in range(16):
        key = f'rx_hw_queue_{queue}_descs'
        count = final[key] - probe['before'][key]
        if not count:
            continue
        if not 0 < count < 2048:
            raise RuntimeError('descriptor window wrapped')
        first = probe['before'][key] & 2047
        record = json.loads(BOARD(f'/tmp/rx-desc-read {queue} {first} {count} marked'))
        record['scanned'] = count
        descriptors.append(record)
    print(json.dumps({'probe': probe, 'descriptors': descriptors, 'raw': raw,
                      'final': final}, indent=2))


if __name__ == '__main__':
    main()
