#!/usr/bin/env python3
"""Observe PHY management-read bursts through existing read-only MDIO stats."""
import json
from pathlib import Path
import re
import runpy

ROOT = Path(__file__).resolve().parents[3]
BOARD = runpy.run_path(str(ROOT / 'port/mainline/network-acceptance/nic-rx-accept.py'))['board']


def main():
    base = '/sys/bus/mdio_bus/devices/'
    wan = base + '14f02000.mdio:01/statistics/reads'
    lan = base + '19000000.ethernet:switch-mii:07/statistics/reads'
    command = 'for i in ' + ' '.join(str(i) for i in range(50))
    command += f'; do read t rest < /proc/uptime; read w < {wan}; read l < {lan}; '
    command += "printf 'POLL %s %s %s\\n' $t $w $l; sleep 0.05; done"
    output = BOARD(command)
    samples = [(float(t), int(w), int(l)) for t, w, l in
               re.findall(r'^POLL ([0-9.]+) (\d+) (\d+)$', output, re.M)]
    intervals = {}
    for index, name in [(1, 'wan'), (2, 'lan1')]:
        times = [b[0] for a, b in zip(samples, samples[1:]) if a[index] != b[index]]
        intervals[name] = [round(b - a, 3) for a, b in zip(times, times[1:])]
    result = {'samples': samples, 'read_burst_intervals_seconds': intervals,
              'sampling_period_seconds': 0.05, 'output': output}
    path = ROOT / 'out/port-led-phy-poll.json'
    path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'path': str(path), 'sample_count': len(samples),
                      'read_burst_intervals_seconds': intervals}, indent=2))
    if len(samples) != 50:
        raise SystemExit('Incomplete polling observation')


if __name__ == '__main__':
    main()
