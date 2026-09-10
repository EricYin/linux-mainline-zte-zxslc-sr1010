#!/usr/bin/env python3
"""Collect static TSO producer evidence without accessing the board."""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess


ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / 'vendor-reference/sr1010-vendor-runtime/modules/files/kmodule/plat_132.ko'
KERNEL = ROOT / 'vendor-reference/2b5/kernel-2b5.elf'


def command(*args):
    return subprocess.run(list(map(str, args)), capture_output=True, text=True, check=True).stdout


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    modules, seen = [], set()
    for path in sorted((ROOT / 'vendor-reference').rglob('*.ko')):
        sha = digest(path)
        if sha in seen:
            continue
        seen.add(sha)
        symbols = command('aarch64-linux-gnu-nm', path)
        hits = [line for line in symbols.splitlines() if re.search(
            r'\b\w*(?:gso|upload|segmentation|(?:^|_)tso(?:_|$)|(?:^|_)mss(?:_|$))\w*$', line, re.I)]
        modules.append({'path': str(path.relative_to(ROOT)), 'sha256': sha, 'symbols': hits})
    assembly = command('aarch64-linux-gnu-objdump', '-dr', '--start-address=0xec3c',
                       '--stop-address=0xef38', MODULE)
    relocations = [line.strip() for line in assembly.splitlines() if 'CALL26' in line]
    upload_flag = command('aarch64-linux-gnu-objdump', '-d',
                          '--start-address=0xffffffc0107a2704',
                          '--stop-address=0xffffffc0107a2734', KERNEL)
    descriptor = command('aarch64-linux-gnu-objdump', '-dr',
                         '--start-address=0xe6a0', '--stop-address=0xe704', MODULE)
    firmware = (ROOT / 'out/mcode_intel.bin').read_bytes()
    loads = []
    mask = (31 << 46) | (127 << 19)
    for pc in range(1920, 2220):
        word = struct.unpack_from('>Q', firmware, 16 + pc * 8)[0]
        if word & ~mask == 0x0700000018000523:
            loads.append({'pc': pc, 'word': f'{word:016x}',
                          'destination_spr': word >> 46 & 31, 'packet_word': word >> 19 & 127})
    samples = []
    for payload, mss in ((500, 1460), (1460, 1460), (2920, 1460), (3000, 1460)):
        repeat, tail, encoded_mss = 0, 0, 0
        if payload > mss:
            repeat = (payload - 1) // mss
            tail = payload - repeat * mss
            encoded_mss = mss
        samples.append({'payload': payload, 'mss': mss, 'word4': repeat << 16,
                        'word5': encoded_mss << 16 | tail,
                        'template_payload_bytes': min(payload, mss),
                        'derived_packet_count': repeat + 1})
    core = (ROOT / 'linux-6.18.38/net/core/dev.c').read_text().splitlines()
    start = next(i for i, line in enumerate(core) if 'TSO requires that SG' in line)
    checks = {
        'module_fingerprint': digest(MODULE) == '6cb3e7c9567b4549b5d1b1c6d87f502d3b07fcde0461c2d28a5ad115661d4d51',
        'firmware_fingerprint': hashlib.sha256(firmware).hexdigest() ==
            '056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40',
        'payload_zero_call': any('edfc:' in line and 'memset' in line for line in relocations),
        'header_copy_call': any('ee38:' in line and 'memcpy' in line for line in relocations),
        'kernel_upload_flag_store': 'orr\tw0, w0, #0x10' in upload_flag and
            'strb\tw0, [x27, #187]' in upload_flag,
    }
    print(json.dumps({'result': 'PASS' if all(checks.values()) else 'FAIL',
                      'scope': 'static evidence consistency, not hardware TSO acceptance',
                      'checks': checks, 'distinct_modules': len(modules), 'modules': modules,
                      'kernel_sha256': digest(KERNEL), 'upload_relocations': relocations,
                      'kernel_upload_flag': upload_flag, 'descriptor_assembly': descriptor,
                      'producer_encoding_examples': samples,
                      'bounded_ppu_local_load_candidates': loads,
                      'linux_feature_dependencies': [{'line': i + 1, 'text': core[i]}
                                                     for i in range(start, start + 18)]}, indent=2))
    return not all(checks.values())


if __name__ == '__main__':
    raise SystemExit(main())
