#!/usr/bin/env python3
"""Audit hardware calibration, per-flow programmable steering and recovery."""
import argparse
from collections import Counter
import ipaddress
import json
from pathlib import Path
import re
import struct


def queue(mode, port, protocol, destination, mask):
    subnet = 1 if mode == 'wan' else 5
    h = (int(ipaddress.ip_address(f'192.168.{subnet}.100')) ^
         int(ipaddress.ip_address(f'192.168.{subnet}.1')) ^
         (port << 16 | destination) ^ protocol)
    for shift in (16, 8, 4, 3):
        h ^= h >> shift
    bucket = h & 7
    return (8 if mode == 'wan' else 0) | (bucket & 3) | ((mask >> bucket & 1) << 2)


def snapshots(trace):
    state = {}
    rows = []
    for line in trace.splitlines():
        match = re.match(r'step=\d+ pc=(\d+) inst=([0-9a-f]+)', line)
        if not match:
            continue
        state.update({int(k): int(v, 16) for k, v in re.findall(r'w(\d+)=([0-9a-f]+)', line)})
        rows.append((int(match[1]), dict(state)))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    checks = []

    def read(name):
        return json.loads((args.directory / (name + '.json')).read_text())

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    def restored(name, trace):
        header = dict(re.findall(r'(\w+)=(-?[0-9a-f]+)', trace.splitlines()[0]))
        check(name + ' context restoration', all(int(header[k], 16) == 0
              for k in ('result', 'restored', 'before', 'after')))

    d = read('calibration-spr10')
    restored('calibration', d['trace'])
    values = [int(v, 16) for _, v in re.findall(r'rss_map_case=(\d+) result=([0-9a-f]+)', d['trace'])]
    check('64 calibrated cases', len(values) == 64)
    for i, value in enumerate(values):
        if i < 32:
            op, n = divmod(i, 8)
            expected = (n >> (op if op < 3 else 0)) & 1
        else:
            op, n = divmod(i - 32, 8)
            expected = ([0, 0xffffffff, 0xa55a3c69, 0x5aa5c396][op] & ~0x70000) | n << 16
        check(f'calibration case {i}', value == expected)
    rejected = read('calibration-rejected-direct-spr')['trace']
    check('direct SPR predicate hypothesis explicitly rejected', all(int(v, 16) == 0
          for n, v in re.findall(r'rss_map_case=(\d+) result=([0-9a-f]+)', rejected) if int(n) < 32))

    errors = ('rx_errors', 'rx_invalid_dma', 'rx_page_lookup_misses',
              'rx_refill_shortfalls', 'tx_timeouts')
    phases = {'default': (0xf0, 0xf0), 'reverse': (0x0f, 0x0f),
              'fixed': (0xff, 0), 'asymmetric': (0xa5, 0x3c),
              'restored': (0xf0, 0xf0), 'recovery': (0xf0, 0xf0)}
    cases = [(f'udp-{p}', masks) for p, masks in phases.items()]
    cases += [(f'{p}-flow-{i}', phases[p]) for p in ('reverse', 'asymmetric') for i in range(8)]
    for name, masks in cases:
        d = read(name)
        count = d['count'] * d['flows']
        expected_row = f'received={count} expected={count} missing=0 bad=0 order=0 duplicate=0 result=PASS'
        check(name + ' sequence integrity', len(d['receiver']) == 2 and
              all(expected_row in row for row in d['receiver']))
        check(name + ' driver errors', all(d['delta'][k] == 0 for k in errors))
        expected = Counter()
        for mode, mask in zip(('wan', 'lan'), masks):
            for flow in range(d['first'], d['first'] + d['flows']):
                expected[queue(mode, 48000 + flow, 17, 49000, mask)] += d['count']
        check(name + ' predicted hardware rings', all(
              d['delta'][f'rx_hw_queue_{q}_descs'] >= packets for q, packets in expected.items()))
        if d['flows'] == 1:
            check(name + ' no competing payload ring', all(
                  d['delta'][f'rx_hw_queue_{q}_descs'] < count for q in range(16) if q not in expected))
        if name == 'udp-fixed':
            check('fixed mapping unused halves stay idle', all(
                  d['delta'][f'rx_hw_queue_{q}_descs'] == 0 for q in range(4, 12)))
            check('fixed mapping worker ownership', d['delta']['rx_napi_0_lan_descs'] == 0 and
                  d['delta']['rx_napi_0_wan_descs'] == 0 and
                  d['delta']['rx_napi_1_lan_descs'] == 4096 and
                  d['delta']['rx_napi_1_wan_descs'] == 4096)

    for phase in ('reverse', 'fixed', 'asymmetric', 'restored'):
        d = read('switch-' + phase)
        check(phase + ' verified install/restore', 'hooks/body restore=0' in d['change'] and
              all('live_read=0 mismatched_blocks=0' in d[k] and 'stopped=00000000' in d[k]
                  for k in ('before', 'change')))
    status = read('program-asymmetric')['status']
    check('416 live instruction words', len(re.findall(r'pc=\d+ inst=', status)) == 416 and
          'live_read=0 mismatched_blocks=0' in status)
    for path in sorted(args.directory.glob('trace-*.json')):
        d = json.loads(path.read_text())
        restored(path.stem, d['trace'])
        rows = snapshots(d['trace'])
        entry = next(((pc, s) for pc, s in rows if pc in (1558, 1893)), None)
        if d['kind'] == 'ipv4-frag-first':
            check('tested fragment bypasses hooks', entry is None and
                  all(not 6000 <= pc < 6500 for pc, _ in rows))
            continue
        pc, before = entry
        after = next(s for at, s in rows if at == pc + 1)
        check(path.stem + ' scalar restoration', all(before[i] == after[i]
              for i in range(64, 96) if i != 80))
        check(path.stem + ' packet words unchanged', all(before[i] == after[i] for i in range(32, 64)))
        if d['kind'] in ('ipv4-udp', 'ipv4-tcp'):
            frame = bytes.fromhex(d['frame'])
            source, destination = struct.unpack('!HH', frame[34:38])
            expected = queue(d['mode'], source, frame[23], destination,
                             0xa5 if d['mode'] == 'wan' else 0x3c)
        else:
            expected = (before[96] >> 26) & 15
        check(path.stem + ' selected queue', (after[80] >> 16) & 15 == expected)
    for phase in ('asymmetric', 'recovery'):
        d = read('tcp-' + phase)
        check(phase + ' 128 MiB TCP', len(d['receiver_rows']) == 16 and all(
              int(size) == 8388608 and int(bad) == 0 and result == 'PASS'
              for _, size, bad, result in d['receiver_rows']))
        check(phase + ' TCP driver errors', all(d['delta'][k] == 0 for k in errors))
    d = read('final')
    check('clean default RAM recovery', 'TAINT\n0\n' in d['state'] and
          'HASH\nY\n' in d['state'] and 'zx279133.rx_hash=0' not in d['state'] and not d['errors'])
    check('both peers reachable', len(d['pings']) == 2 and all(p['exit'] == 0 for p in d['pings'].values()))
    result = 'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL'
    print(json.dumps({'result': result, 'checks': checks}, indent=2))
    return result != 'PASS'


if __name__ == '__main__':
    raise SystemExit(main())
