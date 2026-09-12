#!/usr/bin/env python3
"""Capture factory phtest MDIO output from serial and compare the saved baseline."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def main():
    raw = subprocess.check_output(['tmux', 'capture-pane', '-pJt',
                                   'sr1010-serial:0', '-S', '-4000'], text=True)
    pattern = r'MDIO read phyaddr:0x1 (?:devad:0x([0-9a-f]+) )?reg:0x([0-9a-f]+) value:0x([0-9a-f]+)'
    regs = {}
    for devad, reg, value in re.findall(pattern, raw, re.I):
        key = f'{int(devad, 16) if devad else 255:02x}.{int(reg, 16):04x}'
        regs[key] = f'{int(value, 16):04x}'
    baseline = json.loads((ROOT / 'out/wan-led-mainline.json').read_text())['registers']
    missing = sorted(set(baseline) - set(regs))
    differences = {key: {'mainline': value, 'factory': regs[key]}
                   for key, value in baseline.items()
                   if key in regs and value != regs[key]}
    result = {'registers': regs, 'missing': missing, 'differences': differences,
              'lines': [line for line in raw.splitlines() if 'MDIO read phyaddr:0x1 ' in line]}
    path = ROOT / 'out/wan-led-factory.json'
    path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'path': str(path), 'matched': len(baseline) - len(missing),
                      'missing': missing, 'differences': differences}, indent=2))


if __name__ == '__main__':
    main()
