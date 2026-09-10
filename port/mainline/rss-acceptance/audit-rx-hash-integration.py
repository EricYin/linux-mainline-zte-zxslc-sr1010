#!/usr/bin/env python3
"""Independently audit saved integrated PPU hash lifecycle acceptance."""
import argparse
import json
from pathlib import Path
import re
import runpy
import zlib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    checks = []

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    def read(name):
        return json.loads((args.directory / (name + '.json')).read_text())

    fw = (root / 'out/mcode_intel.bin').read_bytes()
    check('firmware fingerprint', len(fw) == 103456 and zlib.crc32(fw) == 0xef1d7647)
    source = (root / 'linux-6.18.38/drivers/net/ethernet/zte/zx279133-rx-hash.h').read_text()
    words = {int(pc): int(word, 16) for word, pc in re.findall(r'0x([0-9a-f]+)ULL, /\* (\d+) \*/', source)}
    saved = json.loads((args.directory.parent / 'formats/guarded-restore.json').read_text())['status']
    live = {int(pc): int(word, 16) for pc, word in re.findall(r'pc=(\d+) inst=([0-9a-f]+)', saved)}
    check('100 instructions match calibrated hardware readback', len(words) == 100 and all(live.get(pc) == word for pc, word in words.items()))
    errors = ('rx_errors', 'rx_invalid_dma', 'rx_page_lookup_misses', 'rx_refill_shortfalls', 'tx_timeouts')
    for name in ('udp-wan', 'udp-lan', 'udp-both', 'xdp-udp', 'xsk-udp', 'after-xsk', 'disabled-udp', 'recovery-udp', 'tcp', 'disabled-tcp', 'recovery-tcp'):
        d = read(name)
        check(name + ' driver errors', all(d['delta'][k] == 0 for k in errors))
        active = int(not name.startswith('disabled'))
        generation = 0 if not active else 2 if name == 'xsk-udp' else 3 if name == 'after-xsk' else 1
        check(name + ' installed generation', all(d[s]['rx_hash_active'] == active and d[s]['rx_hash_loads'] == generation for s in ('before', 'after')))
        if 'receiver_rows' in d:
            check(name + ' 128 MiB byte integrity', len(d['receiver_rows']) == 16 and all(int(n) == 8388608 and int(bad) == 0 and result == 'PASS' for _, n, bad, result in d['receiver_rows']))
            continue
        check(name + ' ordered UDP integrity', len(d['receiver']) == (1 if d['mode'] != 'both' else 2) and all('received=4096 expected=4096 missing=0 bad=0 order=0 duplicate=0 result=PASS' in row for row in d['receiver']))
        ports = ('wan', 'lan') if d['mode'] == 'both' else (d['mode'],)
        if name == 'xsk-udp':
            check('UMEM single worker fallback', d['delta']['rx_napi_1_polls'] == 0 and all(d['delta']['rx_napi_0_' + p + '_descs'] >= 4096 for p in ports))
        elif active:
            check(name + ' both workers per port', all(d['delta'][f'rx_napi_{w}_{p}_descs'] >= 2048 for w in (0, 1) for p in ports))
        else:
            check('disabled native queues', all(d['delta'][f'rx_hw_queue_{q}_descs'] == (4096 if q in (0, 8) else 0) for q in range(16)))
    xdp = read('xdp-queues')['xdp']
    check('XDP actual queue IDs', dict(re.findall(r'id=(\d+) packets=(\d+)', xdp)) == {'0': '2048', '1': '2048', '2': '0'} and 'XDP_PASS_DETACHED' in xdp)
    for name, generation in (('attach', 2), ('detach', 3)):
        text = read('xsk-' + name)[name]
        check('XSK ' + name + ' automatic installation', 'rx_hash_active: 1' in text and f'rx_hash_loads: {generation}' in text)
    check('XSK completions', 'rx=10 tx=10 completions=10 bytes=1120 rx_dropped=0 rx_invalid=0 tx_invalid=0 rx_ring_full=0' in read('xsk-detach')['detach'])
    d = read('xsk-wire')
    check('XSK ten exact echoes from queue 15', len(d['frames']) == 10 and all(f == d['echo'] for f in d['frames']) and d['delta']['rx_hw_queue_15_descs'] == 10 and d['echo'] == d['wire'][12:24] + d['wire'][:12] + d['wire'][24:])
    d = read('flow')
    hardware = runpy.run_path(str(Path(__file__).with_name('audit-offload-interaction.py')))['hardware']
    check('hardware flow two directions', [(a[0]-b[0], a[1]-b[1]) for b, a in zip(hardware(d['before']), hardware(d['after']))] == [(39168, 128), (38656, 128)])
    check('flow endpoint integrity', len(d['endpoints']) == 2 and all(e['packets'] == 128 and e['bytes'] == 32768 and e['bad'] == 0 and not e['error'] for e in d['endpoints'].values()))
    check('flow driver errors', all(d['delta'][k] == 0 for k in errors))
    d = read('recovery-final')
    check('clean recovery', 'TAINT\n0\n' in d['state'] and 'HASH\nY\n' in d['state'] and not d['errors'] and 'rx_ppu' not in d['state'] and 'zx279133.rx_hash=0' not in d['state'])
    check('both links reachable', all(p['exit'] == 0 for p in d['pings'].values()) and len(d['pings']) == 2)
    result = {'result': 'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL', 'checks': checks}
    print(json.dumps(result, indent=2))
    return result['result'] != 'PASS'


if __name__ == '__main__':
    raise SystemExit(main())
