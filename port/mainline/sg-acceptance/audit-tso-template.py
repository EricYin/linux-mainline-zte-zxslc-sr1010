#!/usr/bin/env python3
"""Verify template wire bytes independently of collector PASS labels."""
import argparse
import json
from pathlib import Path
import re
import runpy
import struct


HERE = Path(__file__).resolve().parent
WIRE = runpy.run_path(str(HERE / 'woe-wire-accept.py'))
CHECKSUM = WIRE['checksum']


def expected_frame(control, index, length, block):
    frame = bytearray(control)
    version = frame[14] >> 4
    iplen = 20 if version == 4 else 40
    tcp = 14 + iplen
    frame = frame[:tcp + 20] + block[:length]
    struct.pack_into('!I', frame, tcp + 4, 0x10203040 + 64 * index)
    if version == 4:
        struct.pack_into('!H', frame, 16, iplen + 20 + length)
        frame[24:26] = bytes(2)
        struct.pack_into('!H', frame, 24, CHECKSUM(frame[14:34]))
        pseudo = frame[26:34] + struct.pack('!BBH', 0, 6, 20 + length)
    else:
        struct.pack_into('!H', frame, 18, 20 + length)
        pseudo = frame[22:54] + struct.pack('!I3xB', 20 + length, 6)
    frame[tcp + 16:tcp + 18] = bytes(2)
    struct.pack_into('!H', frame, tcp + 16, CHECKSUM(pseudo + frame[tcp:]))
    return bytes(frame)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--patterns', action='store_true')
    args = parser.parse_args()
    checks, observations = [], []

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    cases = [('ipv4-stream-control', 4, 64, False, False), ('ipv6-control', 6, 64, False, False)]
    cases += [(f'ipv{v}-template-{p}', v, p, True, False) for v in (4, 6) for p in (64, 128, 145)]
    if args.patterns:
        cases += [(f'ipv{v}-pattern-128', v, 128, True, True) for v in (4, 6)]
    for name, version, payload, template, patterned in cases:
        directory = args.directory / name
        record = json.loads((directory / 'record.json').read_text())
        packets = []
        for frame, original in WIRE['pcap_packets']((directory / 'wire.pcap').read_bytes()):
            tcp = 34 if version == 4 else 54
            if frame[12:14] != (b'\x08\x00' if version == 4 else b'\x86\xdd'):
                continue
            if frame[tcp:tcp + 4] == struct.pack('!HH', 49041, 49042):
                packets.append(frame)
        count = (payload + 63) // 64
        check(name + ' source submission', 'PROBE_EXIT=0' in record['probe'] and 'retained=1' in record['probe'])
        check(name + ' captured control plus outputs', len(packets) == count + 1)
        check(name + ' capture loss', '0 packets dropped by kernel' in record['capture_end'])
        words = {int(i): int(v, 16) for i, v in re.findall(r'desc\[(\d+)\]=([0-9a-f]+)', record['probe'])}
        check(name + ' source descriptor mode', words.get(6) == (0x0c000000 if template else 0x08000000))
        check(name + ' source descriptor count and lengths', words.get(4) == (count - 1) << 16 and
              words.get(5) == ((64 << 16 | (payload - 64 * (count - 1))) if count > 1 else 0))
        deltas = {int(v) for v in re.findall(r'done_after=\d+ delta=(\d+)', record['probe'])}
        check(name + ' one observed completion', deltas == {1})
        check(name + ' verified RAM reload', 'sr1010-zxdbg#' in record['reboot'] and
              'Link is Up - 2.5Gbps' in record['reboot'])
        a = bytes(0x31 + (i & 15) for i in range(64)) if patterned else bytes(64)
        b = bytes(0x71 + (i & 15) for i in range(64)) if patterned else bytes(64)
        behavior = 'unmatched'
        if len(packets) == count + 1:
            control = packets[0]
            check(name + ' driver control bytes', control == expected_frame(control, 0, 64, a))
            repeated = [expected_frame(control, i, min(64, payload - i * 64), a) for i in range(count)]
            advancing = [expected_frame(control, i, min(64, payload - i * 64), a if i == 0 else b)
                         for i in range(count)]
            behavior = 'repeated-first' if packets[1:] == repeated else 'advancing' if packets[1:] == advancing else 'unmatched'
            check(name + ' full output bytes', behavior != 'unmatched')
        observations.append({'case': name, 'output_packets': max(0, len(packets) - 1),
                             'payload_behavior': behavior if patterned else 'zero-template',
                             'completion_deltas': sorted(deltas)})
    print(json.dumps({'result': 'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL',
                      'scope': 'bounded template behavior; not Linux TSO/TSO6 acceptance',
                      'checks': checks, 'observations': observations}, indent=2))
    return any(c['result'] == 'FAIL' for c in checks)


if __name__ == '__main__':
    raise SystemExit(main())
