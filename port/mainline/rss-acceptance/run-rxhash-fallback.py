#!/usr/bin/env python3
"""RAM-only fingerprint mismatch and skb hash recovery acceptance."""
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(HERE / 'run-rx-hash-export.py'))
BOARD, STATS = HELPERS['BOARD'], HELPERS['STATS']
FW = '/lib/firmware/zte/zx279133/mcode_intel.bin'
SHA = '056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40'


def snapshot():
    return {'stats': STATS(BOARD('/tmp/ethtool-test -S eth0')),
            'state': BOARD(f'sha256sum {FW}; /tmp/ethtool-test -k eth0 | grep receive-hashing; '
                           '/tmp/ethtool-test -k lan-cpu0 | grep receive-hashing; '
                           '/tmp/ethtool-test -x eth0; /tmp/ethtool-test -x lan-cpu0')}


def packets(expected):
    results = {}
    for mode in ('wan', 'lan'):
        result = subprocess.run([sys.executable, str(HERE / 'run-continuous-output.py'),
                                 '--integrated', '--skb-only', '--skb-hash', expected,
                                 '--mode', mode, '--kind', 'ipv4-udp', '--count', '128'],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        results[mode] = json.loads(result.stdout)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--persistent', action='store_true')
    args = parser.parse_args()
    prefix = f'/tmp/rss-rxhash-fw-{os.getpid()}'
    report = {'persistent': args.persistent, 'before': snapshot()}
    if SHA not in report['before']['state'] or not report['before']['stats']['rx_hash_active']:
        raise RuntimeError('requires calibrated active firmware')
    BOARD('tftp -g -r rss-set -l /tmp/rss-set 192.168.1.100; chmod +x /tmp/rss-set')
    BOARD(f'cp {FW} {prefix}.backup; cp {FW} {prefix}.good')
    error = None
    try:
        # PC7000 lies in the unloaded gap [5272,12208); change its padding byte.
        restore_args = '' if args.persistent else f' {prefix}.good {FW}'
        report['fault'] = BOARD(f"printf '\\101' | dd of={FW} bs=1 seek=56023 count=1 conv=notrunc; "
                                f'/tmp/rss-set eth0 15{restore_args} > {prefix}.set 2>&1; '
                                f'cat {prefix}.set')
        report['after_fault'] = snapshot()
        report['ready'] = BOARD('for i in 1 2 3 4 5 6 7 8 9 10; do '
                                '[ "$(cat /sys/class/net/eth0/carrier)" = 1 ] && break; '
                                'sleep 1; done; sleep 1; '
                                'cat /sys/class/net/eth0/carrier /sys/class/net/lan1/carrier')
        report['during'] = packets('zero' if args.persistent else 'expected')
    except Exception as failure:
        error = str(failure)
    finally:
        report['file_restore'] = BOARD(f'cp {prefix}.backup {FW}; sha256sum {FW}; '
                                       f'rm -f {prefix}.backup {prefix}.good {prefix}.set')
    if error:
        report['error'] = error
        print(json.dumps(report, indent=2))
        return 1
    if args.persistent:
        report['reload_start'] = BOARD('/tmp/xsk-zc eth0 0 49999 > /tmp/rss-fw-xsk.txt 2>&1 '
                                       '& rss_fw_xsk=$!; sleep 7; head -1 /tmp/rss-fw-xsk.txt')
        report['reload_stop'] = BOARD('kill -TERM $rss_fw_xsk; wait $rss_fw_xsk; '
                                      'unset rss_fw_xsk; sleep 7; cat /tmp/rss-fw-xsk.txt')
    report['recovered'] = packets('expected')
    report['after'] = snapshot()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    raise SystemExit(main())
