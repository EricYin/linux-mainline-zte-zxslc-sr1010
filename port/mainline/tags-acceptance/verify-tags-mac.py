#!/usr/bin/env python3
"""Compare factory tags to all Ethernet MACs without modifying flash."""
import base64
import hashlib
import json
from pathlib import Path
import re
import runpy
import struct
import zlib

ROOT = Path(__file__).resolve().parents[3]
BOARD = runpy.run_path(str(ROOT / 'port/mainline/network-acceptance/nic-rx-accept.py'))['board']


def main():
    devices = BOARD('cat /proc/mtd')
    match = re.search(r'^(mtd\d+):.*"tags"$', devices, re.M)
    if not match:
        raise RuntimeError('tags partition is absent')
    device = '/dev/' + match[1] + 'ro'
    header = base64.b64decode(BOARD(f'dd if={device} bs=12 count=1 2>/dev/null | base64'))
    magic, length, crc = struct.unpack('<III', header)
    if magic != 0x33333333 or length > 65536:
        raise RuntimeError('invalid or oversized acceptance fixture')
    blob = base64.b64decode(BOARD(f'dd if={device} bs={12 + length} count=1 2>/dev/null | base64'))
    if len(blob) != 12 + length or zlib.crc32(blob[12:]) != crc:
        raise RuntimeError('tags length/CRC mismatch')
    pos = 12
    macs = {}
    while pos < len(blob):
        key_len, value_len, _, _ = struct.unpack_from('<IIII', blob, pos)
        pos += 16
        if not key_len or pos + key_len + value_len > len(blob):
            raise RuntimeError('truncated tags entry')
        key = blob[pos:pos + key_len]
        value = blob[pos + key_len:pos + key_len + value_len]
        if key_len == 2 and int.from_bytes(key, 'little') in (0x100, 0x101):
            macs[f'0x{int.from_bytes(key, "little"):04x}'] = value.hex(':')
        pos += key_len + value_len
    expected = {'eth0': macs['0x0100'],
                **{name: macs['0x0101'] for name in ('lan-cpu0', 'lan1', 'lan2', 'lan3', 'lan4')}}
    state = BOARD('for n in eth0 lan-cpu0 lan1 lan2 lan3 lan4; do '
                  'printf "%s " "$n"; cat /sys/class/net/$n/address; done; '
                  'cat /proc/sys/kernel/tainted; '
                  'dmesg | grep -E "zte-tags|factory MAC|deferred probe"')
    actual = dict(re.findall(r'^(eth0|lan-cpu0|lan[1-4]) ([0-9a-f:]{17})$', state, re.M))
    passed = actual == expected and macs['0x0100'] != macs['0x0101']
    fingerprint = BOARD(f'sha256sum {device}')
    result = {'pass': passed, 'device': device, 'payload_length': length,
              'crc_valid': True, 'table_sha256': hashlib.sha256(blob).hexdigest(),
              'partition_sha256': fingerprint.split()[0], 'tags_macs': macs,
              'interfaces': actual, 'state': state}
    output = ROOT / 'out/tags-mac-acceptance.json'
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(f'{output}: {"PASS" if passed else "FAIL"} {len(actual)}/6 interfaces')
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
