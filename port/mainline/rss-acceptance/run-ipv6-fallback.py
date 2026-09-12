#!/usr/bin/env python3
"""Verify exact IPv6 extension/fragment delivery with no skb hardware hash."""
import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
FORMATS = runpy.run_path(str(HERE / 'run-format-probe.py'))
BOARD, STATS = FORMATS['BOARD'], FORMATS['STATS']
READ_STATS = runpy.run_path(str(HERE / 'run-ipv6-rss.py'))['read_stats']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('wan', 'lan'), required=True)
    parser.add_argument('--kind', choices=('ipv6-options', 'ipv6-frag-first', 'ipv6-frag-later'), required=True)
    args = parser.parse_args()
    frame = FORMATS['make_frame'](args.mode, args.kind, BOARD('cat /sys/class/net/eth0/address').strip())
    dev = 'eth0' if args.mode == 'wan' else 'lan1'
    ready = BOARD(f'/tmp/rx-raw-verify {dev} {frame.hex()} 0 64 > /tmp/rss-ipv6-fallback.txt 2>&1 '
                  '& rss_ipv6_fallback=$!; sleep 0.1; cat /tmp/rss-ipv6-fallback.txt')
    if 'RAW_VERIFY_READY' not in ready:
        raise RuntimeError(ready)
    before = READ_STATS()
    command = (['sudo', '-n', sys.executable, str(HERE / 'inject-trace-frame.py'), '--interface', 'en8']
               if args.mode == 'wan' else
               ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', 'cnjn@192.168.3.43', 'python',
                'C:/Users/cnjn/AppData/Local/Temp/sr1010-inject-trace-frame.py', '--interface',
                r'\Device\NPF_{908BA7F3-F82E-4E1E-92C6-573623C048F9}'])
    try:
        sent = subprocess.run(command + ['--frame', frame.hex(), '--count', '64'],
                              capture_output=True, text=True, timeout=20, check=True)
    finally:
        raw = BOARD('for i in 1 2 3 4 5 6 7 8 9 10; do '
                    'grep -q "RAW_VERIFY received=" /tmp/rss-ipv6-fallback.txt && break; sleep 0.2; done; '
                    'kill -TERM $rss_ipv6_fallback 2>/dev/null; wait $rss_ipv6_fallback; '
                    'cat /tmp/rss-ipv6-fallback.txt; unset rss_ipv6_fallback')
    after = READ_STATS(before)
    delta = {key: after[key] - value for key, value in before.items()}
    received = re.search(r'RAW_VERIFY received=(\d+) bad=(\d+) socket_drops=(\d+)', raw)
    checks = {'sent': int(sent.stdout.strip()) == 64,
              'exact_frames_with_zero_skb_hash': received is not None and
              tuple(map(int, received.groups())) == (64, 0, 0),
              'no_hash_reporting': 0 <= delta['rx_hw_hash_packets'] <=
              sum(delta[f'rx_hw_queue_{queue}_descs'] for queue in range(16)) - 64,
              'stable_generation': before['rx_hash_loads'] == after['rx_hash_loads'],
              'no_errors': all(delta[key] == 0 for key in ('rx_errors', 'tx_errors', 'tx_timeouts'))}
    result = 'PASS' if all(checks.values()) else 'FAIL'
    print(json.dumps({'result': result, 'mode': args.mode, 'kind': args.kind, 'checks': checks,
                      'frame': frame.hex(), 'raw': raw, 'before': before, 'after': after, 'delta': delta}, indent=2))
    return 0 if result == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
