#!/usr/bin/env python3
"""Save the mainline WAN PHY register inventory from the serial console."""
import json
from pathlib import Path
import re
import runpy

ROOT = Path(__file__).resolve().parents[3]
BOARD = runpy.run_path(str(ROOT / 'port/mainline/network-acceptance/nic-rx-accept.py'))['board']


def main():
    setup = BOARD('tftp -g -r wan-led-read.ko -l /tmp/wan-led-read.ko '
                  '192.168.1.100 >/tmp/wan-led-tftp.log 2>&1 && '
                  'insmod /tmp/wan-led-read.ko; sleep 3')
    output = BOARD('dmesg | grep wan-led; rmmod wan_led_read; '
                   'cat /sys/class/net/eth0/carrier')
    regs = dict(re.findall(r'wan-led ([0-9a-f]{2}\.[0-9a-f]{4})=([0-9a-f]{4})', output))
    path = ROOT / 'out/wan-led-mainline.json'
    path.write_text(json.dumps({'setup': setup, 'output': output, 'registers': regs}, indent=2) + '\n')
    print(f'{path}: {len(regs)} unique registers')


if __name__ == '__main__':
    main()
