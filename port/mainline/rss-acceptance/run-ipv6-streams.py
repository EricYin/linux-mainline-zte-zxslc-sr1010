#!/usr/bin/env python3
"""Exercise ordered UDP and eight TCP byte streams over existing IPv6 link-local addresses."""
import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
TOOLS = runpy.run_path(str(HERE / 'run-ipv6-rss.py'))
BOARD, STATS = TOOLS['BOARD'], TOOLS['STATS']
READ_STATS = TOOLS['read_stats']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('wan', 'lan'), required=True)
    parser.add_argument('--lan-scope', type=int)
    args = parser.parse_args()
    if args.mode == 'lan' and not args.lan_scope:
        parser.error('provide the verified Windows LAN interface index')
    dev = 'eth0' if args.mode == 'wan' else 'lan1'
    addresses = BOARD(f'ip -6 addr show dev {dev}')
    local = re.search(r'inet6 (fe80:[0-9a-f:]+)/64', addresses)[1]
    target = local + ('%en8' if args.mode == 'wan' else '%' + str(args.lan_scope))
    warm = (['ping6', '-c', '1', target] if args.mode == 'wan' else
            ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', 'cnjn@192.168.3.43',
             'ping', '-6', '-n', '1', '-w', '2000', target])
    prewarm = subprocess.run(warm, capture_output=True, text=True, errors='replace', timeout=10)
    if prewarm.returncode:
        raise RuntimeError('IPv6 neighbor prewarm failed: ' + prewarm.stdout + prewarm.stderr)
    records = {}
    for kind, receiver, script, arguments in (
            ('udp', '/tmp/rss-udp-sequence-ipv6 :: 0 8 512', 'sr1010-udp6-send.py', ['::', target]),
            ('tcp', '/tmp/nic-rx-stream-ipv6 :: 49300', 'sr1010-tcp6-send.py', [target])):
        ready = BOARD(receiver + f' > /tmp/rss-ipv6-{kind}-stream.txt 2>&1 & rss_ipv6_stream=$!; '
                      f'sleep 0.1; cat /tmp/rss-ipv6-{kind}-stream.txt')
        if 'READY' not in ready:
            raise RuntimeError(ready)
        before = READ_STATS()
        if args.mode == 'wan':
            source = HERE / 'udp-sequence-send.py' if kind == 'udp' else HERE.parent / 'network-acceptance/nic-rx-send.py'
            command = [sys.executable, str(source), *arguments]
        else:
            command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', 'cnjn@192.168.3.43',
                       'python', 'C:/Users/cnjn/AppData/Local/Temp/' + script, *arguments]
        try:
            sent = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=90)
        finally:
            received = BOARD(f'wait $rss_ipv6_stream; cat /tmp/rss-ipv6-{kind}-stream.txt; unset rss_ipv6_stream')
        after = READ_STATS(before)
        delta = {key: after[key] - value for key, value in before.items()}
        base = 8 if args.mode == 'wan' else 0
        groups = [sum(delta[f'rx_hw_queue_{base + group * 4 + bit}_descs'] for bit in range(4)) for group in range(2)]
        if kind == 'udp':
            payload_ok = 'received=4096 expected=4096 missing=0 bad=0 order=0 duplicate=0 result=PASS' in received
        else:
            rows = re.findall(r'RX_STREAM id=(\d+) bytes=(\d+) bad=(\d+) result=(\w+)', received)
            payload_ok = len(rows) == 8 and {row[0] for row in rows} == set(map(str, range(8))) and all(
                row[1:] == ('8388608', '0', 'PASS') for row in rows)
        checks = {'sender': sent.returncode == 0, 'payload_and_order': payload_ok,
                  'both_rx_queue_groups': all(groups), 'hardware_hash_reported': delta['rx_hw_hash_packets'] > 0,
                  'no_errors': all(delta[key] == 0 for key in ('rx_errors', 'tx_errors', 'tx_timeouts'))}
        records[kind] = {'checks': checks, 'receiver': received, 'sender': sent.stdout,
                         'stderr': sent.stderr, 'before': before, 'after': after, 'groups': groups}
    passed = all(all(record['checks'].values()) for record in records.values())
    print(json.dumps({'result': 'PASS' if passed else 'FAIL', 'mode': args.mode,
                      'destination': target, 'prewarm': prewarm.stdout, 'records': records}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
