#!/usr/bin/env python3
"""Audit baseline metadata and bounded PPU-to-DMA hash export evidence."""
import argparse
import json
from pathlib import Path
import re
import runpy
import struct


def folded(source, destination, sport, dport, protocol):
    value = source ^ destination ^ (sport << 16 | dport) ^ protocol
    for shift in (16, 8, 4, 3):
        value ^= value >> shift
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    snapshots = runpy.run_path(str(Path(__file__).with_name('audit-rss-map.py')))['snapshots']
    checks, values = [], set()
    baseline = {'wan': set(), 'lan': set()}

    def read(name):
        return json.loads((args.directory / (name + '.json')).read_text())

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    for flow in range(8):
        d = read(f'flow-{flow}')
        traffic = d['traffic']
        check(f'flow {flow} payload integrity', len(traffic['receiver']) == 2 and all(
              'received=16 expected=16 missing=0 bad=0 order=0 duplicate=0 result=PASS'
              in row for row in traffic['receiver']))
        value = folded(0xc0a80164, 0xc0a80101, 48000 + flow, 49000, 17)
        values.add(value)
        for port, cap in d['captures'].items():
            check(f'{port} flow {flow} stable slots', cap['first'] == cap['second'] and
                  len(cap['first']['slots']) == 16 and all(s['words'][0] == 0 for s in cap['first']['slots']))
            check(f'{port} flow {flow} consumer indices', cap['first']['slots'][0]['index'] ==
                  (traffic['before'][f'rx_hw_queue_{cap["first"]["queue"]}_descs'] & 2047))
            for slot in cap['first']['slots']:
                baseline[port].add(tuple(slot['words'][1:]))
    check('eight distinct expected hashes', len(values) == 8)
    check('same metadata despite changed hashes', all(len(v) == 1 for v in baseline.values()))
    d = read('dma-write-width')
    for port, record in d['records'].items():
        check(port + ' stamp before new consumption', d['before'][f'rx_hw_queue_{record["queue"]}_descs'] ==
              d['stamped_counters'][f'rx_hw_queue_{record["queue"]}_descs'])
        check(port + ' DMA overwrites tail words', record['stamped']['slots'][0]['words'][5:] ==
              [0x11223344, 0x55667788, 0x99aabbcc] and
              record['after']['slots'][0]['words'][5] != 0x11223344 and
              record['after']['slots'][0]['words'][6:] == [0, 0])
    exports, raw_runs = 0, 0
    paths = list(args.directory.glob('export-*.json')) + list(args.directory.glob('raw-export-*.json'))
    paths += list(args.directory.glob('verified-*.json')) + list(args.directory.glob('check-*.json'))
    for path in sorted(paths):
        d = json.loads(path.read_text())
        probe = d['probe']
        header = dict(re.findall(r'(\w+)=(-?[0-9a-f]+)', probe['trace'].splitlines()[0]))
        check(path.stem + ' trace restoration', all(int(header[k], 16) == 0
              for k in ('result', 'restored', 'before', 'after')))
        records = d.get('descriptors', [d.get('descriptor')])
        marked = [(record['queue'], slot) for record in records for slot in record['slots']]
        match = re.search(r'hash_export=1 exported=(\d+) value=([0-9a-f]+)', probe['trace'])
        check(path.stem + ' diagnostic enabled', match is not None)
        if not int(match[1]):
            check(path.stem + ' no fallback marker', not marked)
            continue
        exports += 1
        frame = bytes.fromhex(probe['frame'])
        source, destination = struct.unpack('!II', frame[26:34])
        sport, dport = struct.unpack('!HH', frame[34:38])
        value = folded(source, destination, sport, dport, frame[23])
        check(path.stem + ' full PPU hash', int(match[2], 16) == value)
        rows = snapshots(probe['trace'])
        before = next(state for pc, state in rows if pc == 2216)
        after = next(state for pc, state in rows if pc == 2217)
        check(path.stem + ' only intended metadata changes', after[13] == value and
              after[12] == before[12] | (1 << 31) and all(before[i] == after[i]
              for i in range(128) if i not in (12, 13)))
        expected_queue = (8 if probe['mode'] == 'wan' else 0) | (value & 7)
        check(path.stem + ' one consumed marked descriptor', len(marked) == 1 and
              marked[0][0] == expected_queue and marked[0][1]['words'][0] == 0)
        words = marked[0][1]['words']
        check(path.stem + ' exact DMA metadata association', words[2:6] ==
              [after[15], after[14], value, before[12] | (1 << 31)] and words[6:] == [0, 0])
        if d.get('raw'):
            raw_runs += 1
            m = re.search(r'RAW_VERIFY received=(\d+) bad=(\d+) socket_drops=(\d+)', d['raw'])
            check(path.stem + ' every frame byte verified', m is not None and
                  int(m[1]) == probe['sent'] and int(m[2]) == int(m[3]) == 0)
    check('at least six byte-verified exports', raw_runs >= 6)
    for name in ('udp-recovery', 'tcp-recovery'):
        d = read(name)
        if 'receiver_rows' in d:
            check(name + ' 128 MiB integrity', len(d['receiver_rows']) == 16 and all(
                  int(size) == 8388608 and int(bad) == 0 and result == 'PASS'
                  for _, size, bad, result in d['receiver_rows']))
        else:
            check(name + ' 8192 ordered packets', len(d['receiver']) == 2 and all(
                  'received=4096 expected=4096 missing=0 bad=0 order=0 duplicate=0 result=PASS'
                  in row for row in d['receiver']))
        check(name + ' driver errors', all(d['delta'][k] == 0 for k in
              ('rx_errors', 'rx_invalid_dma', 'rx_page_lookup_misses', 'rx_refill_shortfalls', 'tx_timeouts')))
    d = read('final')
    check('clean default RAM recovery', 'TAINT\n0\n' in d['state'] and 'HASH\nY\n' in d['state'] and not d['errors'])
    check('both peers reachable', len(d['pings']) == 2 and all(p['exit'] == 0 for p in d['pings'].values()))
    result = 'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL'
    print(json.dumps({'result': result, 'exports': exports, 'raw_runs': raw_runs, 'checks': checks}, indent=2))
    return result != 'PASS'


if __name__ == '__main__':
    raise SystemExit(main())
