#!/usr/bin/env python3
"""Audit fingerprint fallback without conflating feature enablement and activity."""
import argparse
import json
from pathlib import Path
import re


SHA = '056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    checks = []

    def read(name):
        return json.loads((args.directory / (name + '.json')).read_text())

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    for name in ('one-read', 'persistent'):
        d = read(name)
        persistent = name == 'persistent'
        before, fault, after = (d[k]['stats'] for k in ('before', 'after_fault', 'after'))
        check(name + ' fixture completed', 'error' not in d)
        check(name + ' request rejected', 'errno=95' in d['fault'] and 'result=FAIL' in d['fault'])
        check(name + ' native firmware reached', d['fault'].count('uncalibrated PPU firmware') ==
              (2 if persistent else 1))
        check(name + ' calibration activity', before['rx_hash_active'] == 1 and
              fault['rx_hash_active'] == (0 if persistent else 1) and after['rx_hash_active'] == 1)
        check(name + ' generations', fault['rx_hash_loads'] == before['rx_hash_loads'] +
              (0 if persistent else 1) and after['rx_hash_loads'] == fault['rx_hash_loads'] +
              (2 if persistent else 0))
        check(name + ' RAM firmware restored', SHA in d['file_restore'] and SHA in d['after']['state'])
        check(name + ' RXHASH stays enabled',
              d['after_fault']['state'].count('receive-hashing: on') == 2)
        tables = re.findall(r'0:\s+((?:[01]\s+){7}[01])', d['after']['state'])
        check(name + ' original maps recovered', len(tables) == 2 and
              all(list(map(int, t.split())) == [0, 0, 0, 0, 1, 1, 1, 1] for t in tables))
        for phase in ('during', 'recovered'):
            for port in ('wan', 'lan'):
                p = d[phase][port]
                expected = 'zero' if persistent and phase == 'during' else 'expected'
                check(f'{name} {phase} {port} bytes and raw skb hash', p['result'] == 'PASS' and
                      all(p['checks'].values()) and p['skb_hash_filter'] == expected and
                      'received=128 bad=0 socket_drops=0' in p['raw'])
                delta = p['after']['rx_hw_hash_packets'] - p['before']['rx_hw_hash_packets']
                check(f'{name} {phase} {port} annotation count',
                      delta == 0 if expected == 'zero' else delta >= 128)
    d = read('udp-final')
    check('UDP recovery 8192 packets', len(d['receiver']) == 2 and all(
          'received=4096 expected=4096 missing=0 bad=0 order=0 duplicate=0 result=PASS'
          in row for row in d['receiver']))
    d = read('tcp-final')
    check('TCP recovery 128 MiB', d['result'] == 'PASS' and d['affinity_unchanged'] and
          len(d['receiver_rows']) == 16 and all(row[1:] == ['8388608', '0', 'PASS']
          for row in d['receiver_rows']))
    d = read('queue-regression')
    check('existing queue configuration regression', d['result'] == 'PASS' and
          len(d['checks']) == 77 and all(c['result'] == 'PASS' for c in d['checks']))
    print(json.dumps({'result': 'PASS' if all(c['result'] == 'PASS' for c in checks)
                     else 'FAIL', 'checks': checks}, indent=2))
    return 0 if all(c['result'] == 'PASS' for c in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
