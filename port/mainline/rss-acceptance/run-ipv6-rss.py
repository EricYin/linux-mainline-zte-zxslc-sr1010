#!/usr/bin/env python3
"""Verify bounded IPv6 frames, hardware rings, exported hash and skb hash."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
FORMATS = runpy.run_path(str(HERE / 'run-format-probe.py'))
BOARD, STATS = FORMATS['BOARD'], FORMATS['STATS']
HASH = runpy.run_path(str(HERE / 'audit-ipv6-hash.py'))['tuple_hash']


def read_stats(expected=()):
    required = set(expected) | {'rx_hash_loads', 'rx_hash_active', 'rx_hw_hash_packets',
                                'rx_errors', 'tx_errors', 'tx_timeouts'}
    required |= {f'rx_hw_queue_{queue}_descs' for queue in range(16)}
    for attempt in range(3):
        stats = STATS(BOARD('/tmp/ethtool-test -S eth0'))
        if required <= stats.keys():
            return stats
    raise RuntimeError('serial statistics incomplete: ' + str(sorted(required - stats.keys())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('wan', 'lan'), default='wan')
    parser.add_argument('--kind', choices=('ipv6-udp', 'ipv6-tcp', 'ipv6-ack'), default='ipv6-udp')
    parser.add_argument('--source', type=ipaddress.IPv6Address)
    parser.add_argument('--destination', type=ipaddress.IPv6Address)
    parser.add_argument('--source-port', type=int, default=48000)
    parser.add_argument('--count', type=int, default=64)
    parser.add_argument('--hash-filter', choices=('auto', 'wrong', 'none', 'zero'), default='auto')
    parser.add_argument('--descriptors', action='store_true')
    parser.add_argument('--physical-map', type=lambda value: int(value, 0),
                        help='previously verified hardware map while shared UMEM reports Linux queue zero')
    args = parser.parse_args()
    if not 1 <= args.count <= 512 or not 1 <= args.source_port <= 65535:
        parser.error('count must be 1..512 and source port 1..65535')
    frame = FORMATS['make_frame'](args.mode, args.kind, BOARD('cat /sys/class/net/eth0/address').strip(),
                                 source_port=args.source_port,
                                 ipv6_source=str(args.source) if args.source else None,
                                 ipv6_destination=str(args.destination) if args.destination else None)
    value = HASH(frame)[-1]
    conduit = 'eth0' if args.mode == 'wan' else 'lan-cpu0'
    dev = 'eth0' if args.mode == 'wan' else 'lan1'
    peer = '192.168.1.100' if args.mode == 'wan' else '192.168.5.100'
    ready = BOARD(f'for i in 1 2 3 4 5 6 7 8 9 10; do '
                  f'[ "$(cat /sys/class/net/{dev}/carrier)" = 1 ] && break; sleep 1; done; '
                  f'for i in 1 2 3 4 5; do ping -c 1 -W 2 {peer} && '
                  '{ echo RSS_LINK_READY; break; }; sleep 1; done')
    if 'RSS_LINK_READY' not in ready:
        raise RuntimeError('link/peer did not recover: ' + ready[-500:])
    table = BOARD(f'/tmp/ethtool-test -x {conduit}')
    entries = re.search(r'(?m)^\s+0:\s+([01](?:\s+[01]){7})', table)
    if entries is None:
        raise RuntimeError('RSS table unavailable')
    mapping = list(map(int, entries[1].split()))
    if args.physical_map is not None and not 0 <= args.physical_map <= 255:
        parser.error('physical map must fit eight bits')
    physical = mapping if args.physical_map is None else [(args.physical_map >> bit) & 1 for bit in range(8)]
    queue = (8 if args.mode == 'wan' else 0) | (value & 3) | (physical[value & 7] << 2)
    before = read_stats()
    if args.descriptors and before['rx_hash_loads'] != 1:
        raise RuntimeError('descriptor index association requires the first firmware generation')
    filter_arg = {'auto': f'{value:x}', 'wrong': f'{value ^ 1:x}', 'none': '-', 'zero': '0'}[args.hash_filter]
    ready = BOARD(f'/tmp/rx-raw-verify {dev} {frame.hex()} {filter_arg} {args.count} '
                  '> /tmp/rss-ipv6-raw.txt 2>&1 & rss_ipv6_raw=$!; sleep 0.1; cat /tmp/rss-ipv6-raw.txt')
    if 'RAW_VERIFY_READY' not in ready:
        raise RuntimeError(ready)
    command = (['sudo', '-n', sys.executable, str(HERE / 'inject-trace-frame.py'), '--interface', 'en8']
               if args.mode == 'wan' else
               ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', 'cnjn@192.168.3.43', 'python',
                'C:/Users/cnjn/AppData/Local/Temp/sr1010-inject-trace-frame.py', '--interface',
                r'\Device\NPF_{908BA7F3-F82E-4E1E-92C6-573623C048F9}'])
    try:
        sent = subprocess.run(command + ['--frame', frame.hex(), '--count', str(args.count)],
                              capture_output=True, text=True, timeout=20, check=True)
    finally:
        raw = BOARD('rss_wait=0; while [ $rss_wait -lt 30 ]; do '
                    'grep -q "RAW_VERIFY received=" /tmp/rss-ipv6-raw.txt && break; '
                    'sleep 0.1; rss_wait=$((rss_wait + 1)); done; '
                    'echo RAW_WAIT=$rss_wait; kill -TERM $rss_ipv6_raw 2>/dev/null; wait $rss_ipv6_raw; '
                    'cat /tmp/rss-ipv6-raw.txt; unset rss_ipv6_raw')
    after = read_stats(before)
    delta = {key: after[key] - value for key, value in before.items()}
    key = f'rx_hw_queue_{queue}_descs'
    slots = []
    if args.descriptors and 0 < delta[key] < 2048:
        for offset in range(0, delta[key], 16):
            start = (before[key] + offset) & 2047
            count = min(16, delta[key] - offset)
            record = json.loads(BOARD(f'/tmp/rx-desc-read {queue} {start} {count} marked'))
            slots.extend(slot for slot in record['slots'] if slot['words'][4] == value)
    received = re.search(r'RAW_VERIFY received=(\d+) bad=(\d+) socket_drops=(\d+)', raw)
    expected = 0 if args.hash_filter == 'wrong' else args.count
    background = sum(delta[f'rx_hw_queue_{ring}_descs'] for ring in range(16)) - args.count
    checks = {
        'sender_count': int(sent.stdout.strip()) == args.count,
        'exact_frames': received is not None and tuple(map(int, received.groups())) == (expected, 0, 0),
        'expected_hardware_ring': delta[key] >= args.count,
        'hardware_hash_reporting': (0 <= delta['rx_hw_hash_packets'] <= background if args.hash_filter == 'zero'
                                    else delta['rx_hw_hash_packets'] >= args.count),
        'stable_generation': before['rx_hash_loads'] == after['rx_hash_loads'],
        'active_hash': before['rx_hash_active'] == after['rx_hash_active'] == 1,
        'no_driver_errors': all(delta[key] == 0 for key in ('rx_errors', 'tx_errors', 'tx_timeouts')),
    }
    if args.descriptors:
        checks['descriptor_hashes'] = len(slots) == args.count
    report = {'result': 'PASS' if all(checks.values()) else 'FAIL', 'checks': checks,
              'mode': args.mode, 'kind': args.kind, 'frame': frame.hex(), 'count': args.count,
              'hash_filter': args.hash_filter, 'hash': value, 'queue': queue, 'map': mapping, 'physical_map': physical,
              'raw': raw, 'before': before, 'after': after, 'delta': delta, 'descriptors': slots}
    print(json.dumps(report, indent=2))
    return 0 if report['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
