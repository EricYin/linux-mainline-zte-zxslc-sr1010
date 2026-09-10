#!/usr/bin/env python3
"""Audit bounded RXHASH integration, switches, and descriptor reuse evidence."""
import argparse
import json
from pathlib import Path
import runpy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    checks = []

    def read(name):
        return json.loads((args.directory / (name + '.json')).read_text())

    def check(name, value):
        checks.append({'check': name, 'result': 'PASS' if value else 'FAIL'})

    for name in ('marked-a', 'marked-b', 'unmarked', 'unmarked-b', 'marked-after'):
        d = read(name)
        check(name, d['result'] == 'PASS' and all(d['checks'].values()))

    def slots(names, valid):
        return {s['index'] for n in names for r in read(n)['descriptors']
                if r['queue'] == 8 for s in r['slots'] if bool(s['words'][5] >> 31) == valid}

    marked = slots(('marked-a', 'marked-b'), True)
    unmarked = slots(('unmarked', 'unmarked-b'), False)
    check('marked to unmarked reuse', len(marked & slots(('unmarked',), False)) >= 1000)
    check('unmarked to marked reuse', len(unmarked & slots(('marked-after',), True)) >= 1000)
    names = [f'integrated-{port}-{kind}' for port in ('wan', 'lan')
             for kind in ('udp', 'ipv4-tcp', 'ipv4-ack')]
    names += ['off-wan', 'off-lan', 'xdp-wan', 'after-reload', 'after-umem',
              'options-skb-zero', 'umem-confirmed', 'umem-confirmed-after']
    for name in names:
        d = read(name)
        check(name + ' exact packet and hash filter', d['result'] == 'PASS' and
              all(d['checks'].values()) and d['skb_hash_filter'] in ('expected', 'zero'))
        delta = d['after']['rx_hw_hash_packets'] - d['before']['rx_hw_hash_packets']
        if d['skb_hash_filter'] == 'expected':
            check(name + ' hardware report counter', delta >= d['sent'])
        if name.startswith('off-'):
            check(name + ' no hardware reports', delta == 0)
        if name in ('xdp-wan', 'umem-confirmed'):
            check(name + ' XDP ran', d['after']['xdp_pass'] - d['before']['xdp_pass'] >= d['sent'])
    d = read('reload-map')
    check('RSS reconfiguration reinstalls programs', d['result'] == 'PASS' and
          d['after']['stats']['rx_hash_loads'] == d['before']['stats']['rx_hash_loads'] + 1)
    check('default RSS map restored', read('restore-map')['result'] == 'PASS')
    snapshots = runpy.run_path(str(Path(__file__).with_name('audit-rss-map.py')))['snapshots']
    for kind in ('ipv4-options', 'vlan', 'qinq', 'pppoe'):
        d = read('trace-wan-' + kind)
        rows = snapshots(d['trace'])
        check(kind + ' terminal tuple', rows[-1][0] == 6655 and
              rows[-1][1][13] == 0xa6993b4b and rows[-1][1][12] & (1 << 31))
    d = read('trace-wan-ipv6-options')
    rows = snapshots(d['trace'])
    check('IPv6 extensions fallback', rows[-1][0] == 6667 and not rows[-1][1][12] & (1 << 31))
    for kind in ('ipv4-bad-checksum', 'ipv4-ihl4', 'ipv4-total-short', 'udp-length-short', 'tcp-doff4'):
        d = read('trace-wan-' + kind)
        check(kind + ' no accepted trace claim', 'result=-61 restored=0' in d['trace'] and
              not snapshots(d['trace']))
    d = read('udp-final')
    check('UDP 8192 integrity', len(d['receiver']) == 2 and all(
          'received=4096 expected=4096 missing=0 bad=0 order=0 duplicate=0 result=PASS'
          in row for row in d['receiver']))
    d = read('tcp-final')
    check('TCP 128 MiB integrity', d['result'] == 'PASS' and d['affinity_unchanged'] and
          len(d['receiver_rows']) == 16 and all(row[1:] == ['8388608', '0', 'PASS']
          for row in d['receiver_rows']))
    print(json.dumps({'result': 'PASS' if all(c['result'] == 'PASS' for c in checks)
                     else 'FAIL', 'checks': checks}, indent=2))
    return 0 if all(c['result'] == 'PASS' for c in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
