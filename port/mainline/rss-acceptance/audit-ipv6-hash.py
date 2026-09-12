#!/usr/bin/env python3
"""Verify actual IPv6 PPU operands, accumulator and register-preserving export."""
import argparse
import json
from pathlib import Path
import re
import runpy
import struct


def tuple_hash(frame):
    network = 18 if frame[12:14] == b'\x81\x00' else 14
    if frame[network - 2:network] != b'\x86\xdd' or frame[network] >> 4 != 6:
        raise ValueError('expected IPv6 Ethernet frame')
    protocol = frame[network + 6]
    if protocol not in (6, 17):
        raise ValueError('basic TCP/UDP only; extensions require separate acceptance')
    words = struct.unpack_from('!8I', frame, network + 8)
    ports = struct.unpack_from('!I', frame, network + 40)[0]
    raw = ports ^ protocol
    for word in words:
        raw ^= word
    folded = raw
    for shift in (16, 8, 4, 3):
        folded ^= folded >> shift
    return words, ports, protocol, raw, folded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    snapshots = runpy.run_path(str(Path(__file__).with_name('audit-rss-map.py')))['snapshots']
    checks, covered = [], set()

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    for path in sorted(args.directory.glob('*.json')):
        data = json.loads(path.read_text())
        probe = data.get('probe', data)
        if not probe.get('ipv6_hash'):
            continue
        label, trace = path.stem, probe['trace']
        covered.add((probe['mode'], probe['kind']))
        words, ports, protocol, raw, folded = tuple_hash(bytes.fromhex(probe['frame']))
        header = dict(re.findall(r'(\w+)=(-?[0-9a-f]+)', trace.splitlines()[0]))
        check(label + ' clean replay exit', all(int(header[key], 16) == 0
              for key in ('result', 'restored', 'before', 'after')))
        check(label + ' computed and exported',
              f'raw={raw:08x} hash={folded:08x}' in trace and
              f'hash_export=1 exported=1 value={folded:08x}' in trace)
        operations = [(int(inst, 16), int(acc, 16), int(operand, 16)) for inst, acc, operand in
                      re.findall(r'hash_op=\d+ inst=([0-9a-f]+) accumulator=([0-9a-f]+) '
                                 r'operand=([0-9a-f]+)', trace)]
        for index, word in enumerate(words):
            instruction = 0x0700000028000523 | ((16 if index else 2) << 46) | ((8 + index * 4) << 17)
            values = [operand if index else acc for inst, acc, operand in operations if inst == instruction]
            check(f'{label} address word {index}', values == [word])
        check(label + ' transport ports', [operand for inst, acc, operand in operations
              if inst == 0x0704000008000523] == [ports])
        check(label + ' next header', [operand & 255 for inst, acc, operand in operations
              if inst == 0x07040000280c0520] == [protocol])
        rows = snapshots(trace)
        before = next((w for pc, w in rows if pc == 2216), None)
        after = next((w for pc, w in rows if pc == 2217), None)
        check(label + ' export preserves other sampled words', before is not None and after is not None and
              after[13] == folded and after[12] == before[12] | (1 << 31) and
              all(before[i] == after[i] for i in range(128) if i not in (12, 13)))
    check('WAN and LAN UDP SYN ACK coverage', covered >= {
        (mode, kind) for mode in ('wan', 'lan') for kind in ('ipv6-udp', 'ipv6-tcp', 'ipv6-ack')})
    passed = all(item['result'] == 'PASS' for item in checks)
    print(json.dumps({'result': 'PASS' if passed else 'FAIL', 'checks': checks,
                      'scope': 'single-context IPv6 hash calibration, not continuous RSS acceptance'}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
