#!/usr/bin/env python3
"""Correlate continuously exported hashes with exact received frames and DMA slots."""
import argparse
import json
from pathlib import Path
import re
import runpy
import struct
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(HERE / 'run-rx-hash-export.py'))
BOARD, STATS, FORMATS = (HELPERS[k] for k in ('BOARD', 'STATS', 'FORMATS'))
FOLDED = runpy.run_path(str(HERE / 'audit-rx-metadata.py'))['folded']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('wan', 'lan'), required=True)
    parser.add_argument('--kind', choices=('ipv4-udp', 'ipv4-tcp', 'ipv4-ack',
                                          'ipv4-options', 'ipv4-frag-first', 'ipv6-udp'), required=True)
    parser.add_argument('--count', type=int, default=512)
    parser.add_argument('--source-port', type=int, default=48000)
    parser.add_argument('--integrated', action='store_true')
    parser.add_argument('--skb-hash', choices=('expected', 'zero'))
    parser.add_argument('--skb-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.count <= 1024 or not 1 <= args.source_port <= 65535:
        parser.error('count must be 1..1024 and source port 1..65535')
    peer = (['ping', '-c', '1', '-S', '192.168.1.100', '192.168.1.1'] if args.mode == 'wan'
            else ['ssh', '-o', 'BatchMode=yes', 'cnjn@192.168.3.43',
                  'ping', '-n', '1', '-w', '2000', '192.168.5.1'])
    for attempt in range(3):
        try:
            ready = subprocess.run(peer, capture_output=True, timeout=5)
        except subprocess.TimeoutExpired:
            continue
        if not ready.returncode and b'ttl=' in ready.stdout.lower():
            break
        time.sleep(1)
    else:
        raise RuntimeError('peer not reachable before measured injection')
    status = BOARD('/tmp/ethtool-test -k eth0' if args.integrated else
                   'head -3 /sys/kernel/debug/rx-ppu-output/status')
    if not args.integrated and ('hooked=1' not in status or 'mismatched_blocks=0' not in status):
        raise RuntimeError(status)
    frame = FORMATS['make_frame'](args.mode, args.kind,
                                 BOARD('cat /sys/class/net/eth0/address').strip(),
                                 False, args.source_port)
    fallback = args.kind in ('ipv4-frag-first', 'ipv6-udp')
    if (fallback and args.skb_hash == 'expected') or (args.skb_only and not args.skb_hash):
        parser.error('fallback fixtures require zero hash; skb-only requires --skb-hash')
    transport = 14 + (frame[14] & 15) * 4
    value = None if fallback else FOLDED(*struct.unpack('!II', frame[26:34]),
                                       *struct.unpack('!HH', frame[transport:transport + 4]), frame[23])
    dev = 'eth0' if args.mode == 'wan' else 'lan1'
    hash_arg = '' if not args.skb_hash else f' {value if args.skb_hash == "expected" else 0:08x}'
    observe_raw = not fallback or args.skb_only
    ready = '' if not observe_raw else BOARD(f'/tmp/rx-raw-verify {dev} {frame.hex()}{hash_arg} > /tmp/rss-raw.txt 2>&1 '
                  '& rss_raw=$!; sleep 0.1; cat /tmp/rss-raw.txt')
    if observe_raw and 'RAW_VERIFY_READY' not in ready:
        raise RuntimeError(ready)
    before = STATS(BOARD('/tmp/ethtool-test -S eth0'))
    command = (['sudo', '-n', sys.executable, str(HERE / 'inject-trace-frame.py'),
                '--interface', 'en8'] if args.mode == 'wan' else
               ['ssh', '-o', 'BatchMode=yes', 'cnjn@192.168.3.43', 'python',
                'C:/Users/cnjn/AppData/Local/Temp/sr1010-inject-trace-frame.py',
                '--interface', r'\Device\NPF_{908BA7F3-F82E-4E1E-92C6-573623C048F9}'])
    try:
        sent = subprocess.run(command + ['--frame', frame.hex(), '--count', str(args.count)],
                              capture_output=True, text=True, timeout=10)
        if sent.returncode:
            raise RuntimeError(sent.stderr or sent.stdout)
    finally:
        raw = '' if not observe_raw else BOARD('sleep 0.2; kill -TERM $rss_raw; wait $rss_raw; '
                    'cat /tmp/rss-raw.txt; unset rss_raw')
    after = STATS(BOARD('/tmp/ethtool-test -S eth0'))
    if not args.skb_only and (before['rx_hash_loads'] != 1 or after['rx_hash_loads'] != 1):
        raise RuntimeError('requires fresh boot without engine reload')
    records = []
    for queue in range(0 if args.skb_only else 16):
        key = f'rx_hw_queue_{queue}_descs'
        count = after[key] - before[key]
        if not count:
            continue
        if not 0 < count < 2048:
            raise RuntimeError('descriptor window wrapped')
        record = {'queue': queue, 'slots': []}
        for offset in range(0, count, 64):
            chunk = json.loads(BOARD(f'/tmp/rx-desc-read {queue} '
                                     f'{(before[key] + offset) & 2047} '
                                     f'{min(64, count - offset)}' +
                                     ('' if fallback else ' marked')))
            record['slots'] += chunk['slots']
        record['scanned'] = count
        records.append(record)
    length = len(frame) + (4 if args.mode == 'lan' else 0)
    matched = [(r['queue'], s) for r in records for s in r['slots']
               if ((s['words'][1] & 0x3fff) == length if fallback else s['words'][4] == value)]
    expected_queue = None if fallback else (8 if args.mode == 'wan' else 0) | (value & 7)
    raw_match = re.search(r'RAW_VERIFY received=(\d+) bad=(\d+) socket_drops=(\d+)', raw)
    checks = {
        'sender': int(sent.stdout.strip()) == args.count,
        'stable_engine': before['rx_hash_loads'] == after['rx_hash_loads'],
    }
    if not args.skb_only:
        checks['descriptor_count'] = len(matched) == args.count
        checks['consumed'] = all(s['words'][0] == 0 for _, s in matched)
    if fallback and not args.skb_only:
        checks['marker_clear'] = all(not (s['words'][5] & (1 << 31)) for _, s in matched)
    if observe_raw:
        checks['raw'] = raw_match is not None and tuple(map(int, raw_match.groups())) == (args.count, 0, 0)
        if not args.skb_only and not fallback:
            checks['queues'] = all(q == expected_queue for q, _ in matched)
    print(json.dumps({'result': 'PASS' if all(checks.values()) else 'FAIL',
                      'checks': checks, 'mode': args.mode, 'kind': args.kind,
                      'frame': frame.hex(), 'hash': value, 'sent': args.count,
                      'skb_hash_filter': args.skb_hash,
                      'skb_only': args.skb_only,
                      'raw': raw, 'before': before, 'after': after,
                      'status': status, 'descriptors': records}, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
