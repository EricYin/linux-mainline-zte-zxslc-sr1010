#!/usr/bin/env python3
"""Independently check RSS closure records, including byte and queue evidence."""
import argparse
import json
from pathlib import Path
import re
import runpy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--continuation', type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    queue = runpy.run_path(str(here / 'audit-rss-map.py'))['queue']
    table = runpy.run_path(str(here / 'audit-rss-config.py'))['table']
    checks = []
    records = {p.name: p for p in args.directory.glob('*.json')}
    if args.continuation:
        records.update({p.name: p for p in args.continuation.glob('*.json')})

    def read(name):
        return json.loads(records[name + '.json'].read_text())

    def check(name, condition):
        checks.append({'check': name, 'result': 'PASS' if condition else 'FAIL'})

    for index, maps in enumerate(((0, 255), (165, 60), (255, 0), (90, 195), (240, 240))):
        name = f'map-{index}'
        d = read(name)
        check(name + ' tables', all(table(d['after'][p]) == [(m >> i) & 1 for i in range(8)]
                                   for p, m in zip(('wan', 'lan'), maps)))
        check(name + ' hash active', d['after']['stats']['rx_hash_active'] == 1)
        d = read(name + '-noop')
        check(name + ' no-op', d['after']['stats']['rx_hash_loads'] == d['before']['stats']['rx_hash_loads'] and
              all('errno=0 result=PASS' in w for w in d['writes']))
    for path in sorted(records.values()):
        d = json.loads(path.read_text())
        if 'receiver' in d and 'sent' in d:
            expected = d['flows'] * d['count']
            check(path.stem + ' datagram integrity', len(d['receiver']) == len(d['sent']) and all(
                  f'received={expected} expected={expected} missing=0 bad=0 order=0 duplicate=0 result=PASS'
                  in row for row in d['receiver']))
            check(path.stem + ' driver state', all(d['delta'][k] == 0 for k in
                  ('rx_errors', 'rx_dropped', 'rx_invalid_dma', 'rx_page_lookup_misses',
                   'rx_refill_shortfalls', 'tx_timeouts')) and d['after']['rx_refill_deficit'] == 0)
            if d['flows'] == 1:
                mode, flow = d['mode'], d['first']
                q = queue(mode, 48000 + flow, 17, 49000, 165 if mode == 'wan' else 60)
                base = 8 if mode == 'wan' else 0
                check(path.stem + ' exclusive payload queue', d['delta'][f'rx_hw_queue_{q}_descs'] >= expected and
                      sum(d['delta'][f'rx_hw_queue_{other}_descs'] for other in range(base, base + 8)
                          if other != q) < 32)
        if d.get('skb_only'):
            check(path.stem + ' exact raw bytes and filtered hash',
                  d['sent'] == 128 and 'received=128 bad=0 socket_drops=0' in d['raw'])
            delta = d['after']['rx_hw_hash_packets'] - d['before']['rx_hw_hash_packets']
            check(path.stem + ' annotation count', delta == 0 if d['skb_hash_filter'] == 'zero' else delta >= 128)
            check(path.stem + ' engine stable', d['after']['rx_hash_loads'] == d['before']['rx_hash_loads'])
    d = read('port-cycle')
    check('port cycle retains independent maps', d['maps'] == [165, 60])
    d = read('umem-active')
    check('UMEM effective queue zero and rejected writes', d['maps'] == [0, 0] and
          d['writes'].count('errno=16 result=FAIL') == 2 and
          d['before']['rx_hash_loads'] == d['after']['rx_hash_loads'])
    check('UMEM detach restores normal maps', read('umem-detached')['maps'] == [240, 240])
    for name, persistent in (('one-read', False), ('persistent', True)):
        d = read(name)
        check(name + ' rejected setting', 'errno=95 result=FAIL' in d['fault'] and 'error' not in d)
        a, b, c = (d[k]['stats'] for k in ('before', 'after_fault', 'after'))
        check(name + ' activity transition', a['rx_hash_active'] == c['rx_hash_active'] == 1 and
              b['rx_hash_active'] == (0 if persistent else 1))
        check(name + ' generations', b['rx_hash_loads'] - a['rx_hash_loads'] == (0 if persistent else 1) and
              c['rx_hash_loads'] - b['rx_hash_loads'] == (2 if persistent else 0))
        sha = '056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40'
        check(name + ' firmware restored', sha in d['file_restore'] and sha in d['after']['state'])
        for phase in ('during', 'recovered'):
            for mode in ('wan', 'lan'):
                p = d[phase][mode]
                expected = 'zero' if persistent and phase == 'during' else 'expected'
                check(f'{name} {phase} {mode} bytes and hash',
                      'received=128 bad=0 socket_drops=0' in p['raw'] and p['skb_hash_filter'] == expected)
    d = read('final-tcp')
    check('final TCP 128 MiB', d['affinity_unchanged'] and len(d['receiver_rows']) == 16 and
          all(row[1:] == ['8388608', '0', 'PASS'] for row in d['receiver_rows']))
    d, initial = read('final'), read('initial')
    check('final maps and affinity', d['maps'] == initial['maps'] and d['affinity'] == initial['affinity'])
    check('final kernel state', d['state'].startswith('0\n') and not re.search(
          r'BUG:|Oops:|Call trace:|NETDEV WATCHDOG|Kernel panic', d['state']))
    check('final hash active', d['stats']['rx_hash_active'] == 1)
    print(json.dumps({'result': 'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL',
                      'checks': checks}, indent=2))
    return any(c['result'] == 'FAIL' for c in checks)


if __name__ == '__main__':
    raise SystemExit(main())
