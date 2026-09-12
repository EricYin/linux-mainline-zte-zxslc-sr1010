#!/usr/bin/env python3
"""Stage RAM-only IPv6 RSS fixtures and restore the established LAN interface."""
import json
from pathlib import Path
import runpy


HERE = Path(__file__).resolve().parent
BOARD = runpy.run_path(str(HERE / 'run-ipv6-rss.py'))['BOARD']


def main():
    output = []
    for name in ('ethtool-test', 'rss-set', 'rx-desc-read', 'rx-raw-verify',
                 'rss-udp-sequence-ipv6', 'nic-rx-stream-ipv6', 'rx-ppu-trace.ko',
                 'nic-xdp-pass', 'xsk-zc', 'rss-udp-sequence', 'nic-rx-stream', 'nic-tx-stream'):
        output.append(BOARD(f'tftp -g -r {name} -l /tmp/{name} 192.168.1.100; chmod +x /tmp/{name}'))
    output.append(BOARD('tftp -g -r libmnl.so.0 -l /lib/libmnl.so.0 192.168.1.100; '
                        'if [ ! -d /sys/class/net/lan1 ]; then '
                        'insmod /lib/modules/6.18.38+/kernel/net/dsa/tag_zx279133_rtl8372n.ko; '
                        'insmod /lib/modules/6.18.38+/kernel/drivers/net/ethernet/zte/zx279133-rtl8372n.ko; fi; '
                        'ip link set lan-cpu0 up; ip link set lan1 up; '
                        'ip addr show dev lan1 | grep -q 192.168.5.1 || ip addr add 192.168.5.1/24 dev lan1; '
                        'echo 1 > /proc/irq/22/smp_affinity; echo 2 > /proc/irq/25/smp_affinity; '
                        'for i in 1 2 3 4 5 6 7 8 9 10; do '
                        '[ "$(cat /sys/class/net/lan1/carrier)" = 1 ] && break; sleep 1; done; '
                        'cat /proc/cmdline; cat /proc/sys/kernel/tainted'))
    print(json.dumps({'output': output}, indent=2))


if __name__ == '__main__':
    main()
