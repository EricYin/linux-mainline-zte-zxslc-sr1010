#!/usr/bin/env python3
"""Audit continuous output, descriptor reuse, and terminal branch evidence."""
import argparse
import json
from pathlib import Path
import re
import runpy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    checks = []

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    def read(name):
        return json.loads((args.directory / (name + '.json')).read_text())

    names = [port + '-' + kind for port in ('wan', 'lan') for kind in ('udp', 'syn', 'ack')]
    names += ['reuse-a', 'reuse-b', 'reuse-c', 'fallback-wan-fragment', 'fallback-lan-ipv6']
    for name in names:
        d = read(name)
        for key, value in d['checks'].items():
            check(name + ' ' + key, value)
        check(name + ' running and matching code', 'stopped=00000000' in d['status'] and
              'live_read=0 mismatched_blocks=0' in d['status'])
    reuse = [read('reuse-' + suffix) for suffix in 'abc']
    slots = [{s['index'] for r in d['descriptors'] for s in r['slots']
              if s['words'][4] == d['hash']} for d in reuse]
    check('three distinct full hashes in one ring', len({d['hash'] for d in reuse}) == 3 and
          len({d['hash'] & 7 for d in reuse}) == 1)
    check('third batch overwrites first batch slots', len(slots[0] & slots[2]) >= 1000)
    snapshots = runpy.run_path(str(Path(__file__).with_name('audit-rss-map.py')))['snapshots']
    for name, end, allowed in [('trace-lan-syn', 6655, (12, 13, 66, 70)),
                               ('trace-wan-fragment', 6667, (12, 66))]:
        d = read(name)
        header = dict(re.findall(r'(\w+)=(-?[0-9a-f]+)', d['trace'].splitlines()[0]))
        check(name + ' restored', all(int(header[k], 16) == 0
              for k in ('result', 'restored', 'before', 'after')))
        rows = snapshots(d['trace'])
        before = next(w for pc, w in rows if pc == 2216)
        pc, after = rows[-1]
        check(name + ' native submit and END reached', pc == end and
              any(p == end - 1 for p, _ in rows))
        check(name + ' preserved destination and other state',
              all(before[i] == after[i] for i in range(128) if i not in allowed))
        check(name + ' expected branch', (any(p == 6596 for p, _ in rows)) == (end == 6655))
    d = read('reload')
    check('module restore and reload', 'hooks/body restore=0' in d['reload'] and
          'INSTALL=0' in d['reload'] and 'mismatched_blocks=0' in d['reload'])
    for phase in ('active', 'recovery'):
        d = read('udp-' + phase)
        check('UDP ' + phase, len(d['receiver']) == 2 and all(
              'received=4096 expected=4096 missing=0 bad=0 order=0 duplicate=0 result=PASS'
              in row for row in d['receiver']))
        d = read('tcp-' + phase)
        check('TCP ' + phase, d['result'] == 'PASS' and d['affinity_unchanged'] and
              len(d['receiver_rows']) == 16 and all(row[1:] == ['8388608', '0', 'PASS']
              for row in d['receiver_rows']))
    print(json.dumps({'result': 'PASS' if all(c['result'] == 'PASS' for c in checks)
                     else 'FAIL', 'checks': checks}, indent=2))
    return 0 if all(c['result'] == 'PASS' for c in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
