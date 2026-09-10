#!/usr/bin/env python3
"""Check terminal hash recomputation and register-preserving export replays."""
import argparse
import json
from pathlib import Path
import re
import runpy
import struct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    snapshots = runpy.run_path(str(here / 'audit-rss-map.py'))['snapshots']
    folded = runpy.run_path(str(here / 'audit-rx-metadata.py'))['folded']
    checks = []

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    paths = sorted(args.directory.glob('normalized-*.json'))
    paths += sorted(args.directory.glob('native-*.json'))
    check('six normalized fixtures', len(list(args.directory.glob('normalized-*.json'))) == 6)
    check('two native export fixtures', len(list(args.directory.glob('native-*.json'))) == 2)
    for path in paths:
        d = json.loads(path.read_text())
        p, label = d['probe'], path.stem
        trace = p['trace']
        header = dict(re.findall(r'(\w+)=(-?[0-9a-f]+)', trace.splitlines()[0]))
        check(label + ' restored', all(int(header[k], 16) == 0
              for k in ('result', 'restored', 'before', 'after')))
        frame = bytes.fromhex(p['frame'])
        value = folded(*struct.unpack('!IIHH', frame[26:38]), frame[23])
        check(label + ' full hash', f'hash_export=1 exported=1 value={value:08x}' in trace)
        check(label + ' output recompute', 'hash_pc=2216' in trace and 'ops=18' in trace)
        if label.startswith('native-'):
            check(label + ' native sequence', 'native_export=1' in trace)
        rows = snapshots(trace)
        before = next(w for pc, w in rows if pc == 2216)
        after = next(w for pc, w in rows if pc == 2217)
        check(label + ' register and metadata preservation', after[13] == value and
              after[12] == before[12] | (1 << 31) and all(before[i] == after[i]
              for i in range(128) if i not in (12, 13)))
        marked = [(r['queue'], s['words']) for r in d['descriptors'] for s in r['slots']]
        expected_queue = (8 if p['mode'] == 'wan' else 0) | (value & 7)
        check(label + ' unique descriptor association', len(marked) == 1 and
              marked[0][0] == expected_queue and marked[0][1][2:6] ==
              [after[15], after[14], after[13], after[12]])
        raw = re.search(r'RAW_VERIFY received=(\d+) bad=(\d+) socket_drops=(\d+)', d['raw'])
        check(label + ' exact frames', raw is not None and int(raw[1]) == p['sent'] and
              int(raw[2]) == 0 and int(raw[3]) == 0)
        if 'metadata_loads=1' in trace:
            check(label + ' local loads',
                  f'word12={before[12]:08x} word13={before[13]:08x}' in trace)
    print(json.dumps({'result': 'PASS' if all(c['result'] == 'PASS' for c in checks)
                     else 'FAIL', 'checks': checks}, indent=2))
    return 0 if all(c['result'] == 'PASS' for c in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
