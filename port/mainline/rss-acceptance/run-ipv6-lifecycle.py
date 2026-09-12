#!/usr/bin/env python3
"""Verify IPv6 RSS through XDP_PASS and shared AF_XDP UMEM transitions."""
import json
from pathlib import Path
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(HERE / 'run-ipv6-rss.py'))
BOARD, READ_STATS = HELPERS['BOARD'], HELPERS['read_stats']


def main():
    records, checks = {}, {}
    error = None

    def traffic(name, mode, *arguments):
        result = subprocess.run([sys.executable, str(HERE / 'run-ipv6-rss.py'), '--mode', mode,
                                 *arguments], capture_output=True, text=True, timeout=45)
        if not result.stdout.startswith('{'):
            raise RuntimeError(name + ': ' + result.stderr[-700:])
        records[name] = json.loads(result.stdout)
        checks[name] = result.returncode == 0 and records[name]['result'] == 'PASS'
        if not checks[name]:
            raise RuntimeError(name + ' failed')

    try:
        # The established driver exposes native XDP on WAN only.
        for mode, dev in (('wan', 'eth0'),):
            ready = BOARD(f'/tmp/nic-xdp-pass {dev} queues > /tmp/rss-ipv6-xdp.txt 2>&1 '
                          '& rss_ipv6_xdp=$!; sleep 0.1; cat /tmp/rss-ipv6-xdp.txt')
            if 'XDP_PASS_READY' not in ready:
                raise RuntimeError(ready)
            for flow in (0, 2):
                traffic(f'{mode}-xdp-{flow}', mode, '--source-port', str(48000 + flow), '--hash-filter', 'zero')
            detached = BOARD('kill -TERM $rss_ipv6_xdp; wait $rss_ipv6_xdp; '
                             'cat /tmp/rss-ipv6-xdp.txt; unset rss_ipv6_xdp')
            records[mode + '-xdp-detach'] = detached
            checks[mode + '-xdp-detached'] = 'XDP_PASS_DETACHED' in detached
            traffic(mode + '-xdp-restored', mode)
        ready = BOARD('/tmp/xsk-zc eth0 0 49999 > /tmp/rss-ipv6-xsk.txt 2>&1 '
                      '& rss_ipv6_xsk=$!; sleep 7; head -3 /tmp/rss-ipv6-xsk.txt')
        if 'AF_XDP zero-copy active' not in ready:
            raise RuntimeError(ready)
        records['umem-start'] = ready
        before = READ_STATS()
        writes = BOARD('/tmp/rss-set eth0 15; /tmp/rss-set lan-cpu0 15')
        after = READ_STATS(before)
        records['umem-writes'] = writes
        checks['umem-map-writes-rejected'] = writes.count('errno=16 result=FAIL') == 2
        checks['umem-rejected-writes-no-reload'] = before['rx_hash_loads'] == after['rx_hash_loads']
        traffic('wan-umem', 'wan', '--hash-filter', 'zero', '--physical-map', '0xf0')
        traffic('lan-umem', 'lan', '--physical-map', '0xf0')
        checks['umem-reports-queue-zero'] = all(records[name]['map'] == [0] * 8 for name in ('wan-umem', 'lan-umem'))
        records['umem-stop'] = BOARD('kill -TERM $rss_ipv6_xsk; wait $rss_ipv6_xsk; '
                                     'unset rss_ipv6_xsk; sleep 7; cat /tmp/rss-ipv6-xsk.txt')
        for mode in ('wan', 'lan'):
            traffic(mode + '-umem-restored', mode)
            checks[mode + '-restored-map'] = records[mode + '-umem-restored']['map'] == [0, 0, 0, 0, 1, 1, 1, 1]
    except Exception as exception:
        error = str(exception)
    finally:
        records['cleanup'] = BOARD('if [ -n "$rss_ipv6_xdp" ]; then kill -TERM $rss_ipv6_xdp; '
                                   'wait $rss_ipv6_xdp; unset rss_ipv6_xdp; fi; '
                                   'if [ -n "$rss_ipv6_xsk" ]; then kill -TERM $rss_ipv6_xsk; '
                                   'wait $rss_ipv6_xsk; unset rss_ipv6_xsk; sleep 7; fi')
    passed = error is None and all(checks.values())
    print(json.dumps({'result': 'PASS' if passed else 'FAIL', 'error': error,
                      'checks': checks, 'records': records}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
