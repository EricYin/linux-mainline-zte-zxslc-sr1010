#!/usr/bin/env python3
"""Apply optional standard RSS configurations and capture both conduit states."""
import argparse
import json
from pathlib import Path
import re
import runpy


HERE = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(HERE.parent / 'network-acceptance/nic-rx-parallel.py'))
BOARD, STATS = HELPERS['BOARD'], HELPERS['stats']


def snapshot():
    return {'stats': STATS(BOARD('/tmp/ethtool-test -S eth0')),
            'wan': BOARD('/tmp/ethtool-test -x eth0; echo EXIT=$?'),
            'lan': BOARD('/tmp/ethtool-test -x lan-cpu0; echo EXIT=$?')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wan', type=lambda value: int(value, 0))
    parser.add_argument('--lan', type=lambda value: int(value, 0))
    args = parser.parse_args()
    if any(mask is not None and not 0 <= mask <= 255 for mask in (args.wan, args.lan)):
        parser.error('maps must fit eight bits')
    before = snapshot()
    writes = []
    for dev, mask in (('eth0', args.wan), ('lan-cpu0', args.lan)):
        if mask is not None:
            writes.append(BOARD(f'sleep 0.1; /tmp/rss-set {dev} {mask} '
                                f'> /tmp/rss-set-{dev}.txt; sleep 0.1; '
                                f'cat /tmp/rss-set-{dev}.txt'))
    ready = BOARD('for i in 1 2 3 4 5 6 7 8 9 10; do '
                  '[ "$(cat /sys/class/net/eth0/carrier)" = 1 ] && break; '
                  'sleep 1; done; cat /sys/class/net/eth0/carrier /sys/class/net/lan1/carrier')
    after = snapshot()
    passed = all(re.search(r'RSS_SET .*errno=0 result=PASS', row) for row in writes)
    print(json.dumps({'result': 'PASS' if passed else 'FAIL', 'before': before,
                      'writes': writes, 'ready': ready, 'after': after}, indent=2))
    return not passed


if __name__ == '__main__':
    raise SystemExit(main())
