#!/usr/bin/env python3
"""Run one interface's IPv6 RSS matrix and restore its map/hash feature."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
BOARD = runpy.run_path(str(HERE / 'run-ipv6-rss.py'))['BOARD']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('wan', 'lan'), required=True)
    parser.add_argument('--phase', choices=('all', 'maps'), default='all')
    parser.add_argument('--observe-first-map', action='store_true')
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    dev = 'eth0' if args.mode == 'wan' else 'lan-cpu0'
    table = BOARD(f'/tmp/ethtool-test -x {dev}')
    values = list(map(int, re.search(r'(?m)^\s+0:\s+([01](?:\s+[01]){7})', table)[1].split()))
    saved_map = sum(value << bit for bit, value in enumerate(values))
    features = BOARD(f'/tmp/ethtool-test -k {dev}')
    saved_hash = re.search(r'receive-hashing: (on|off)', features)[1]
    results = []

    def settle():
        link = BOARD('for i in 1 2 3 4 5 6 7 8 9 10; do '
                     '[ "$(cat /sys/class/net/eth0/carrier)" = 1 ] && '
                     '{ echo RSS_SHARED_LINK_READY; break; }; sleep 1; done')
        if 'RSS_SHARED_LINK_READY' not in link:
            raise RuntimeError('shared WAN datapath did not settle after RSS reload')

    def case(name, *extra):
        result = subprocess.run([sys.executable, str(HERE / 'run-ipv6-rss.py'), '--mode', args.mode,
                                 *extra], capture_output=True, text=True, timeout=45)
        (args.directory / (name + '.json')).write_text(result.stdout)
        if result.returncode:
            (args.directory / (name + '.error.txt')).write_text(result.stderr)
            raise RuntimeError(name + ': ' + result.stderr[-500:])
        record = json.loads(result.stdout)
        results.append({'case': name, 'result': record['result'], 'queue': record['queue']})
        print(name + ' PASS queue=' + str(record['queue']), flush=True)

    try:
        BOARD(f'/tmp/ethtool-test -K {dev} rxhash on; /tmp/rss-set {dev} 0xf0')
        settle()
        for kind in (() if args.phase == 'maps' else ('ipv6-udp', 'ipv6-tcp', 'ipv6-ack')):
            for flow in range(8):
                case(f'{kind}-port-{flow}', '--kind', kind, '--source-port', str(48000 + flow))
        network = 1 if args.mode == 'wan' else 5
        for endpoint, suffix in (() if args.phase == 'maps' else (('source', '100'), ('destination', '1'))):
            base = int(ipaddress.IPv6Address(f'2001:db8:{network}::{suffix}'))
            for word in range(4):
                address = ipaddress.IPv6Address(base ^ (1 << (96 - word * 32)))
                case(f'{endpoint}-word-{word}', '--' + endpoint, str(address))
        for mapping in (0x00, 0xff, 0xa5):
            observing = args.observe_first_map and mapping == 0
            if observing:
                user_dev = 'eth0' if args.mode == 'wan' else 'lan1'
                BOARD(f'/tmp/rx-hash-observe {user_dev} > /tmp/rss-map-observer.txt 2>&1 & rss_map_observer=$!')
            try:
                BOARD(f'/tmp/rss-set {dev} {mapping}')
                settle()
                for flow in range(8):
                    case(f'map-{mapping:02x}-port-{flow}', '--source-port', str(48000 + flow))
            finally:
                if observing:
                    observed = BOARD('kill -TERM $rss_map_observer; wait $rss_map_observer; '
                                     'cat /tmp/rss-map-observer.txt; unset rss_map_observer')
                    (args.directory / 'first-map-observer.json').write_text(json.dumps({'output': observed}, indent=2))
        BOARD(f'/tmp/rss-set {dev} 0xf0')
        settle()
        case('wrong-skb-hash', '--hash-filter', 'wrong')
        BOARD(f'/tmp/ethtool-test -K {dev} rxhash off')
        case('rxhash-off', '--hash-filter', 'zero')
        BOARD(f'/tmp/ethtool-test -K {dev} rxhash on')
        case('rxhash-restored')
    finally:
        restored = BOARD(f'/tmp/rss-set {dev} {saved_map}; /tmp/ethtool-test -K {dev} rxhash {saved_hash}; '
                         f'/tmp/ethtool-test -x {dev}; /tmp/ethtool-test -k {dev}')
        (args.directory / 'restored.json').write_text(json.dumps({'map': saved_map, 'hash': saved_hash,
                                                               'output': restored}, indent=2) + '\n')
    (args.directory / 'matrix.json').write_text(json.dumps({'result': 'PASS', 'cases': results}, indent=2) + '\n')


if __name__ == '__main__':
    main()
