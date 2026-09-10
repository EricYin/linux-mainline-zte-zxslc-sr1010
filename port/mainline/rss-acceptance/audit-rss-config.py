#!/usr/bin/env python3
"""Audit writable RSS state, packet steering, rollback and flow preservation."""
import argparse
import json
from pathlib import Path
import re
import runpy


def table(text):
    match = re.search(r'(?m)^\s+0:\s+([01 ]+)$', text)
    return list(map(int, match[1].split())) if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    queue = runpy.run_path(str(here / 'audit-rss-map.py'))['queue']
    hardware = runpy.run_path(str(here / 'audit-offload-interaction.py'))['hardware']
    checks = []

    def read(name):
        return json.loads((args.directory / (name + '.json')).read_text())

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    header = (root / 'linux-6.18.38/drivers/net/ethernet/zte/zx279133-rx-hash.h').read_text()
    recorded = json.loads((args.directory.parent / 'map/program-asymmetric.json').read_text())['status']
    expected = {int(pc): int(v, 16) for pc, v in re.findall(r'pc=(\d+) inst=([0-9a-f]+)', recorded)}
    words = {}
    for part in header.split('.hook = ')[1:]:
        if int(part.split(',', 1)[0]) not in (1558, 1893):
            continue
        start = int(re.search(r'\.start = (\d+)', part)[1])
        program = {int(pc): int(v, 16) for v, pc in re.findall(r'0x([0-9a-f]+)ULL, /\* (\d+) \*/', part)}
        for offset, bucket, port in re.findall(r'\{ (\d+), (\d+), (\d+) \}', part):
            pc = start + int(offset)
            bit = ([0xa5, 0x3c][int(port)] >> int(bucket)) & 1
            program[pc] = (program[pc] & ~(1 << 27)) | bit << 27
        words.update(program)
    check('416 queue-selection instructions match calibrated hardware', len(words) == 416 and words == expected)
    for name, maps in (
            ('fixed', (255, 0)), ('noop', (255, 0)),
            ('wan-asymmetric', (0xa5, 0)), ('asymmetric-confirm', (0xa5, 0x3c)),
            ('final-asymmetric', (0xa5, 0x3c)), ('final-after-xsk', (0xa5, 0x3c)),
            ('flow-removed-config', (0xa5, 0x3c)), ('restored-default', (0xf0, 0xf0)),
            ('recovery', (0xf0, 0xf0))):
        d = read(name)
        check(name + ' reported maps', all(table(d['after'][port]) ==
              [(mask >> i) & 1 for i in range(8)] for port, mask in zip(('wan', 'lan'), maps)))
        check(name + ' active hash', d['after']['stats']['rx_hash_active'] == 1)
    for name in ('noop', 'asymmetric-confirm'):
        d = read(name)
        check(name + ' no restart', d['before']['stats']['rx_hash_loads'] == d['after']['stats']['rx_hash_loads'])
    for name in ('xsk-rejected', 'final-xsk-rejected', 'flow-rejected'):
        d = read(name)
        check(name + ' EBUSY without restart', all('errno=16 result=FAIL' in row for row in d['writes']) and
              d['before']['stats']['rx_hash_loads'] == d['after']['stats']['rx_hash_loads'])
        check(name + ' maps unchanged', all(table(d['before'][p]) == table(d['after'][p]) for p in ('wan', 'lan')))
    d = read('netlink-cli')
    check('standard netlink setter', 'SET_EXIT=0' in d['output'] and table(d['after']['wan']) == [0, 1] * 4 and
          table(d['before']['lan']) == table(d['after']['lan']))
    check('unsupported function and fields rejected', 'BAD_HASH_EXIT=1' in d['output'] and 'BAD_FIELDS_EXIT=1' in d['output'])
    d = read('rollback-success')
    check('one-read failure returns error and restores file', 'errno=95 result=FAIL' in d['output'] and
          'RSS_FW_RESTORE result=PASS' in d['output'] and '056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40' in d['output'])
    check('old maps restored after failure', all(table(d['before'][p]) == table(d['after'][p]) for p in ('wan', 'lan')) and
          d['after']['stats']['rx_hash_active'] == 1 and
          d['after']['stats']['rx_hash_loads'] == d['before']['stats']['rx_hash_loads'] + 1)
    d = read('degraded')
    check('persistent mismatch restores native RX', 'errno=95 result=FAIL' in d['output'] and
          d['after']['stats']['rx_hash_active'] == 0 and 'Not supported' in d['after']['wan'])
    errors = ('rx_errors', 'rx_invalid_dma', 'rx_page_lookup_misses', 'rx_refill_shortfalls', 'tx_timeouts')
    for name in ('udp-fixed', 'udp-xsk', 'udp-degraded', 'udp-after-rollback', 'udp-recovery'):
        d = read(name)
        check(name + ' 8192 ordered packets', len(d['receiver']) == 2 and all(
              'received=4096 expected=4096 missing=0 bad=0 order=0 duplicate=0 result=PASS' in row for row in d['receiver']))
        check(name + ' driver errors', all(d['delta'][k] == 0 for k in errors))
    d = read('udp-degraded')
    check('native fallback queues', d['delta']['rx_hw_queue_0_descs'] >= 4096 and d['delta']['rx_hw_queue_8_descs'] >= 4096)
    for i in range(8):
        d = read(f'final-flow-{i}')
        expected = [queue(p, 48000 + i, 17, 49000, m) for p, m in (('wan', 0xa5), ('lan', 0x3c))]
        check(f'flow {i} sequence integrity', len(d['receiver']) == 2 and all(
              'received=512 expected=512 missing=0 bad=0 order=0 duplicate=0 result=PASS' in row for row in d['receiver']))
        check(f'flow {i} physical ring selection', all(d['delta'][f'rx_hw_queue_{q}_descs'] >= 512 for q in expected) and
              all(d['delta'][f'rx_hw_queue_{q}_descs'] < 512 for q in range(16) if q not in expected))
        check(f'flow {i} driver errors', all(d['delta'][k] == 0 for k in errors))
    for name in ('tcp-after-rollback', 'tcp-recovery'):
        d = read(name)
        check(name + ' 128 MiB integrity', len(d['receiver_rows']) == 16 and all(
              int(size) == 8388608 and int(bad) == 0 and result == 'PASS' for _, size, bad, result in d['receiver_rows']))
        check(name + ' driver errors', all(d['delta'][k] == 0 for k in errors))
    for name in ('flow-before', 'flow-after'):
        d = read(name)
        delta = [(a[0]-b[0], a[1]-b[1]) for b, a in zip(hardware(d['before']), hardware(d['after']))]
        check(name + ' hardware hits preserved', delta == [(39168, 128), (38656, 128)])
        check(name + ' endpoint integrity', len(d['endpoints']) == 2 and all(
              e['packets'] == 128 and e['bytes'] == 32768 and not e['bad'] and not e['error'] for e in d['endpoints'].values()))
    d = read('final')
    check('clean default recovery', 'TAINT\n0\n' in d['state'] and 'HASH\nY\n' in d['state'] and not d['errors'])
    check('both peers reachable', len(d['pings']) == 2 and all(p['exit'] == 0 for p in d['pings'].values()))
    result = 'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL'
    print(json.dumps({'result': result, 'checks': checks}, indent=2))
    return result != 'PASS'


if __name__ == '__main__':
    raise SystemExit(main())
