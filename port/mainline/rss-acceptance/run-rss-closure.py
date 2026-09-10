#!/usr/bin/env python3
"""Run bounded RSS correctness closure and restore the existing RAM fixture."""
import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
PERF = runpy.run_path(str(HERE / 'run-rss-performance.py'))
BOARD, STATS, TABLE = PERF['BOARD'], PERF['STATS'], PERF['table']
QUEUE = runpy.run_path(str(HERE / 'audit-rss-map.py'))['queue']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--lifecycle-only', action='store_true', help='resume at UMEM after earlier map/format evidence was collected')
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    checks = []

    def save(name, data):
        (args.directory / (name + '.json')).write_text(json.dumps(data, indent=2) + '\n')

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})
        print(json.dumps(checks[-1]), flush=True)

    def run(name, script, *arguments, timeout=120):
        process = subprocess.run([sys.executable, str(HERE / script), *map(str, arguments)],
                                 capture_output=True, text=True, timeout=timeout)
        try:
            data = json.loads(process.stdout)
        except json.JSONDecodeError:
            data = {'stdout': process.stdout, 'stderr': process.stderr}
        save(name, data)
        check(name + ' completed', process.returncode == 0 and 'error' not in data and
              data.get('result', 'PASS') == 'PASS')
        if process.returncode:
            raise RuntimeError(name + ': ' + (process.stderr or process.stdout)[-1000:])
        return data

    def stats():
        return STATS(BOARD('/tmp/ethtool-test -S eth0'))

    def hashes(prefix, expected='expected', kinds=('ipv4-udp',)):
        for mode in ('wan', 'lan'):
            for kind in kinds:
                selected = expected[mode] if isinstance(expected, dict) else expected
                expectation = 'zero' if kind in ('ipv4-options', 'ipv4-frag-first', 'ipv6-udp') else selected
                d = run(f'{prefix}-{mode}-{kind}', 'run-continuous-output.py',
                        '--integrated', '--skb-only', '--skb-hash', expectation,
                        '--mode', mode, '--kind', kind, '--count', 128)
                check(f'{prefix}-{mode}-{kind} exact bytes and hash',
                      'received=128 bad=0 socket_drops=0' in d['raw'] and all(d['checks'].values()))

    original = (TABLE('eth0'), TABLE('lan-cpu0'))
    affinity_cmd = ('cat /proc/irq/22/smp_affinity /proc/irq/25/smp_affinity; '
                    'cat /sys/class/net/eth0/queues/rx-*/rps_cpus '
                    '/sys/class/net/lan-cpu0/queues/rx-*/rps_cpus; taskset -p $$')
    affinity = BOARD(affinity_cmd)
    features = BOARD('/tmp/ethtool-test -k eth0; /tmp/ethtool-test -k lan-cpu0')
    if features.count('receive-hashing: on') != 2:
        raise RuntimeError('requires both RXHASH features enabled')
    save('initial', {'maps': original, 'affinity': affinity, 'features': features, 'stats': stats()})
    try:
        staged = BOARD('tftp -g -r rx-raw-verify -l /tmp/rx-raw-verify 192.168.1.100; '
                       'chmod +x /tmp/rx-raw-verify')
        save('staged', {'output': staged})
        map_cases = () if args.lifecycle_only else ((0, 255), (0xa5, 0x3c), (255, 0), (0x5a, 0xc3), (240, 240))
        for index, maps in enumerate(map_cases):
            name = f'map-{index}'
            run(name, 'run-rss-config.py', '--wan', maps[0], '--lan', maps[1])
            check(name + ' readback', (TABLE('eth0'), TABLE('lan-cpu0')) == maps)
            run(name + '-sequence', 'run-udp-sequence.py')
            before = stats()
            run(name + '-noop', 'run-rss-config.py', '--wan', maps[0], '--lan', maps[1])
            check(name + ' no-op generation', stats()['rx_hash_loads'] == before['rx_hash_loads'])
            if index == 1:
                for mode, mask in zip(('wan', 'lan'), maps):
                    for flow in range(8):
                        d = run(f'{name}-{mode}-flow-{flow}', 'run-udp-sequence.py',
                                '--mode', mode, '--first', flow, '--flows', 1)
                        q = QUEUE(mode, 48000 + flow, 17, 49000, mask)
                        check(f'{name}-{mode}-flow-{flow} predicted hardware queue',
                              d['delta'][f'rx_hw_queue_{q}_descs'] >= 512)
                cycle = BOARD('ip link set eth0 down; ip link set lan1 down; sleep 1; '
                              'ip link set lan1 up; ip link set eth0 up; sleep 7')
                save('port-cycle', {'output': cycle, 'maps': [TABLE('eth0'), TABLE('lan-cpu0')]})
                check('port-cycle retains asymmetric maps', (TABLE('eth0'), TABLE('lan-cpu0')) == maps)
                run('port-cycle-sequence', 'run-udp-sequence.py')
                hashes('port-cycle')
        if not args.lifecycle_only:
            hashes('formats', kinds=('ipv4-udp', 'ipv4-tcp', 'ipv4-ack', 'ipv4-options',
                                    'ipv4-frag-first', 'ipv6-udp'))
            before = stats()
            off = BOARD('/tmp/ethtool-test -K eth0 rxhash off; /tmp/ethtool-test -K lan-cpu0 rxhash off')
            save('hash-disabled', {'output': off})
            hashes('hash-off', 'zero')
            check('disabled hash has no annotations', stats()['rx_hw_hash_packets'] == before['rx_hw_hash_packets'])
            BOARD('/tmp/ethtool-test -K eth0 rxhash on; /tmp/ethtool-test -K lan-cpu0 rxhash on')
            hashes('hash-restored')
        xsk = BOARD('/tmp/xsk-zc eth0 0 49999 > /tmp/rss-closure-xsk.txt 2>&1 '
                    '& rss_closure_xsk=$!; sleep 7; head -1 /tmp/rss-closure-xsk.txt')
        before = stats()
        writes = BOARD('/tmp/rss-set eth0 15; /tmp/rss-set lan-cpu0 15')
        effective = (TABLE('eth0'), TABLE('lan-cpu0'))
        save('umem-active', {'start': xsk, 'writes': writes, 'maps': effective,
                             'before': before, 'after': stats()})
        check('UMEM writes rejected without reload', writes.count('errno=16 result=FAIL') == 2 and
              stats()['rx_hash_loads'] == before['rx_hash_loads'] and effective == (0, 0))
        hashes('umem-pass', {'wan': 'zero', 'lan': 'expected'})
        stop = BOARD('kill -TERM $rss_closure_xsk; wait $rss_closure_xsk; '
                     'unset rss_closure_xsk; sleep 7; cat /tmp/rss-closure-xsk.txt')
        save('umem-detached', {'output': stop, 'maps': [TABLE('eth0'), TABLE('lan-cpu0')]})
        check('UMEM detach restores maps', (TABLE('eth0'), TABLE('lan-cpu0')) == (240, 240))
        hashes('umem-restored')
        run('one-read', 'run-rxhash-fallback.py')
        run('persistent', 'run-rxhash-fallback.py', '--persistent')
    except Exception as error:
        save('error', {'error': str(error)})
        check('suite completed without interruption', False)
    finally:
        cleanup = BOARD('if [ -n "$rss_closure_xsk" ]; then kill -TERM $rss_closure_xsk; '
                        'wait $rss_closure_xsk; unset rss_closure_xsk; sleep 7; fi; '
                        '/tmp/ethtool-test -K eth0 rxhash on; /tmp/ethtool-test -K lan-cpu0 rxhash on; '
                        'ip link set lan1 up; ip link set eth0 up')
        save('cleanup', {'output': cleanup})
        run('restore', 'run-rss-config.py', '--wan', original[0], '--lan', original[1])
        run('final-sequence', 'run-udp-sequence.py')
        run('final-tcp', '../network-acceptance/nic-rx-payload-accept.py', '--keep-affinity')
        state = BOARD('cat /proc/sys/kernel/tainted; cat /proc/modules; '
                      'dmesg | grep -E "BUG:|Oops:|Call trace:|NETDEV WATCHDOG|Kernel panic"; '
                      'sha256sum /lib/firmware/zte/zx279133/mcode_intel.bin')
        save('final', {'maps': [TABLE('eth0'), TABLE('lan-cpu0')], 'affinity': BOARD(affinity_cmd),
                       'state': state, 'stats': stats()})
        check('final maps restored', (TABLE('eth0'), TABLE('lan-cpu0')) == original)
        check('affinities unchanged', BOARD(affinity_cmd) == affinity)
        check('kernel clean', state.startswith('0\n') and not re.search(
              r'BUG:|Oops:|Call trace:|NETDEV WATCHDOG|Kernel panic', state))
        save('checks', {'result': 'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL',
                        'checks': checks})
    return any(c['result'] == 'FAIL' for c in checks)


if __name__ == '__main__':
    raise SystemExit(main())
