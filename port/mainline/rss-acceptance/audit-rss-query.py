#!/usr/bin/env python3
"""Audit standard RSS query output against the saved board traffic evidence."""
import argparse
import json
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    checks = []

    def read(name):
        return json.loads((args.directory / (name + '.json')).read_text())

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    for phase, generation in (('on', 1), ('xsk', 2), ('after-xsk', 3),
                              ('off', 0), ('recovery', 1)):
        d = read(phase)
        check(phase + ' installation state',
              f'rx_hash_loads: {generation}' in d['state'] and
              f'rx_hash_active: {int(phase != "off")}' in d['state'])
        for dev in ('eth0', 'lan-cpu0'):
            table = d[dev + '/table']
            if phase == 'off':
                check(phase + dev + ' no active table', 'EXIT=1' in table and
                      'Not supported' in table)
            else:
                row = re.search(r'(?m)^\s+0:\s+([01 ]+)$', table)
                expected = [0] * 8 if phase == 'xsk' else [0] * 4 + [1] * 4
                check(phase + dev + ' Linux queue mapping', row is not None and
                      list(map(int, row[1].split())) == expected and
                      '2 RX ring(s)' in table and 'xor: on' in table and 'EXIT=0' in table)
            for flow in ('tcp4', 'udp4', 'tcp6', 'udp6'):
                fields = d[dev + '/' + flow]
                expected = ('None',) if phase == 'off' or flow.endswith('6') else (
                    'L3 proto', 'IP SA', 'IP DA', 'L4 bytes 0 & 1', 'L4 bytes 2 & 3')
                check(phase + dev + flow + ' fields', 'EXIT=0' in fields and
                      all(f in fields for f in expected))
            check(phase + dev + ' unsupported writes rejected', all(
                  'EXIT=1' in d[dev + '/' + kind] and
                  'Not supported' in d[dev + '/' + kind]
                  for kind in ('set-table', 'set-fields')))
    errors = ('rx_errors', 'rx_invalid_dma', 'rx_page_lookup_misses',
              'rx_refill_shortfalls', 'tx_timeouts')
    for phase in ('on', 'xsk', 'off', 'recovery'):
        d = read('udp-' + phase)
        check(phase + ' 8192 ordered packets', len(d['receiver']) == 2 and all(
              'received=4096 expected=4096 missing=0 bad=0 order=0 duplicate=0 result=PASS'
              in row for row in d['receiver']))
        check(phase + ' driver errors', all(d['delta'][k] == 0 for k in errors))
        if phase == 'off':
            check('native queue delivery', all(
                  d['delta'][f'rx_hw_queue_{q}_descs'] >= 4096 if q in (0, 8)
                  else d['delta'][f'rx_hw_queue_{q}_descs'] == 0
                  for q in range(16)))
        elif phase == 'xsk':
            check('UMEM single worker delivery', d['delta']['rx_napi_1_polls'] == 0 and
                  all(d['delta'][f'rx_napi_0_{port}_descs'] >= 4096 for port in ('wan', 'lan')))
        else:
            check(phase + ' two workers per interface', all(
                  d['delta'][f'rx_napi_{worker}_{port}_descs'] >= 2048
                  for worker in (0, 1) for port in ('wan', 'lan')))
    for phase in ('on', 'recovery'):
        d = read('tcp-' + phase)
        check(phase + ' 128 MiB TCP integrity', len(d['receiver_rows']) == 16 and all(
              int(size) == 8388608 and int(bad) == 0 and result == 'PASS'
              for _, size, bad, result in d['receiver_rows']))
        check(phase + ' TCP driver errors', all(d['delta'][k] == 0 for k in errors))
    d = read('final')
    check('default recovery', 'zx279133.rx_hash=0' not in d['state'] and
          'TAINT\n0\n' in d['state'] and not d['errors'])
    check('both peers reachable', len(d['pings']) == 2 and
          all(p['exit'] == 0 for p in d['pings'].values()))
    result = 'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL'
    print(json.dumps({'result': result, 'checks': checks}, indent=2))
    return result != 'PASS'


if __name__ == '__main__':
    raise SystemExit(main())
