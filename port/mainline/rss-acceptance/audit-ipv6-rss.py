#!/usr/bin/env python3
"""Independently audit both interfaces' saved IPv6 RSS traffic matrix."""
import argparse
import json
from pathlib import Path
import re
import runpy


HERE = Path(__file__).resolve().parent
HASH = runpy.run_path(str(HERE / 'audit-ipv6-hash.py'))['tuple_hash']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wan', type=Path, nargs='+', required=True)
    parser.add_argument('--lan', type=Path, nargs='+', required=True)
    args = parser.parse_args()
    checks = []
    expected = {f'{kind}-port-{flow}' for kind in ('ipv6-udp', 'ipv6-tcp', 'ipv6-ack') for flow in range(8)}
    expected |= {f'{endpoint}-word-{word}' for endpoint in ('source', 'destination') for word in range(4)}
    expected |= {f'map-{mapping:02x}-port-{flow}' for mapping in (0, 255, 165) for flow in range(8)}
    expected |= {'wrong-skb-hash', 'rxhash-off', 'rxhash-restored'}

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    for mode in ('wan', 'lan'):
        records = {}
        for directory in getattr(args, mode):
            for name in expected:
                path = directory / (name + '.json')
                if path.exists():
                    records[name] = json.loads(path.read_text())
        check(mode + ' complete 59-case coverage', set(records) == expected)
        for name, record in sorted(records.items()):
            label = mode + '/' + name
            value = HASH(bytes.fromhex(record['frame']))[-1]
            queue = (8 if mode == 'wan' else 0) | (value & 3) | (record['map'][value & 7] << 2)
            before, after = record['before'], record['after']
            raw = re.search(r'RAW_VERIFY received=(\d+) bad=(\d+) socket_drops=(\d+)', record['raw'])
            count = record['count']
            check(label + ' interface and independently calculated queue', record['mode'] == mode and
                  record['hash'] == value and record['queue'] == queue)
            check(label + ' exact payload and skb filter', raw is not None and tuple(map(int, raw.groups())) ==
                  (0 if record['hash_filter'] == 'wrong' else count, 0, 0))
            check(label + ' actual hardware ring', after[f'rx_hw_queue_{queue}_descs'] -
                  before[f'rx_hw_queue_{queue}_descs'] >= count)
            changed = after['rx_hw_hash_packets'] - before['rx_hw_hash_packets']
            background = sum(after[f'rx_hw_queue_{q}_descs'] - before[f'rx_hw_queue_{q}_descs']
                             for q in range(16)) - count
            check(label + ' driver hardware hash reporting',
                  0 <= changed <= background if name == 'rxhash-off' else changed >= count)
            check(label + ' stable active engine', before['rx_hash_loads'] == after['rx_hash_loads'] and
                  before['rx_hash_active'] == after['rx_hash_active'] == 1)
            check(label + ' error counters unchanged', all(after[key] == before[key]
                  for key in ('rx_errors', 'tx_errors', 'tx_timeouts')))
            if name.startswith('map-'):
                mapping = int(name.split('-')[1], 16)
                check(label + ' requested mapping read back', record['map'] == [(mapping >> bit) & 1 for bit in range(8)])
        for kind in ('ipv6-udp', 'ipv6-tcp', 'ipv6-ack'):
            rings = {records[f'{kind}-port-{flow}']['queue'] for flow in range(8)
                     if f'{kind}-port-{flow}' in records}
            check(f'{mode} {kind} all eight hardware rings', rings == set(range(8, 16) if mode == 'wan' else range(8)))
    passed = all(check['result'] == 'PASS' for check in checks)
    print(json.dumps({'result': 'PASS' if passed else 'FAIL', 'passed': sum(c['result'] == 'PASS' for c in checks),
                      'total': len(checks), 'checks': checks}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
