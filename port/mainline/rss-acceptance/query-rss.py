#!/usr/bin/env python3
"""Capture standard ethtool RSS queries and rejected writes on the RAM board."""
import json
from pathlib import Path
import runpy


def main():
    board = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                              'network-acceptance/nic-rx-accept.py'))['board']
    records = {}
    for dev in ('eth0', 'lan-cpu0'):
        for label, arguments in (
                ('table', '-x'),
                ('tcp4', '-n'), ('udp4', '-n'),
                ('tcp6', '-n'), ('udp6', '-n'),
                ('set-table', '-X'), ('set-fields', '-N')):
            suffix = (' rx-flow-hash ' + label if label in
                      ('tcp4', 'udp4', 'tcp6', 'udp6') else
                      ' equal 2' if label == 'set-table' else
                      ' rx-flow-hash tcp4 sd' if label == 'set-fields' else '')
            records[dev + '/' + label] = board(
                '/tmp/ethtool-test ' + arguments + ' ' + dev + suffix +
                '; echo EXIT=$?')
    records['state'] = board('cat /sys/module/zx279133/parameters/rx_hash; '
                             '/tmp/ethtool-test -S eth0 | tail -2; '
                             'cat /proc/sys/kernel/tainted')
    print(json.dumps(records, indent=2))


if __name__ == '__main__':
    main()
