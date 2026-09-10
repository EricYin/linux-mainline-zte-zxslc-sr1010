#!/usr/bin/env python3
"""Run the bounded IPv4/IPv6 template matrix with a RAM reboot per case."""
import json
from pathlib import Path
import runpy
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'out/acceptance/tso-template'


def case(version, payload, ordinary=False):
    name = f'ipv{version}-' + ('control' if ordinary else f'template-{payload}')
    directory = OUT / name
    args = [sys.executable, str(HERE / 'run-tso-template.py'), '--version', str(version),
            '--payload', str(payload), '--directory', str(directory)]
    if ordinary:
        args.append('--ordinary')
    result = subprocess.run(args, capture_output=True, text=True, timeout=180)
    (OUT / (name + '-process.json')).write_text(json.dumps(
        {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}, indent=2) + '\n')
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    record = json.loads((directory / 'record.json').read_text())
    print(json.dumps({'case': name, 'result': record['result'],
                      'checks': record['checks'], 'frames': len(record['frames'])}), flush=True)
    return record['result'] == 'PASS'


def recover():
    board = runpy.run_path(str(ROOT / 'port/mainline/network-acceptance/nic-rx-accept.py'))['board']
    for name in ('ethtool-test', 'rss-set', 'rss-udp-sequence', 'nic-rx-stream', 'nic-tx-stream'):
        board(f'tftp -g -r {name} -l /tmp/{name} 192.168.1.100; chmod +x /tmp/{name}')
    setup = board('tftp -g -r libmnl.so.0 -l /lib/libmnl.so.0 192.168.1.100; '
                  'if [ ! -d /sys/class/net/lan1 ]; then '
                  'insmod /lib/modules/6.18.38+/kernel/net/dsa/tag_zx279133_rtl8372n.ko; '
                  'insmod /lib/modules/6.18.38+/kernel/drivers/net/ethernet/zte/zx279133-rtl8372n.ko; fi; '
                  'ip link set lan-cpu0 up; ip link set lan1 up; '
                  'ip addr show dev lan1 | grep -q 192.168.5.1 || ip addr add 192.168.5.1/24 dev lan1; '
                  'echo 1 > /proc/irq/22/smp_affinity; echo 2 > /proc/irq/25/smp_affinity; '
                  'for i in 1 2 3 4 5 6 7 8 9 10; do '
                  '[ "$(cat /sys/class/net/lan1/carrier)" = 1 ] && break; sleep 1; done; '
                  'ping -c 1 -W 2 192.168.5.100')
    (OUT / 'recovery-setup.json').write_text(json.dumps({'output': setup}, indent=2) + '\n')
    for name, script, extra in (
            ('udp-final', ROOT / 'port/mainline/rss-acceptance/run-udp-sequence.py', []),
            ('tcp-final', ROOT / 'port/mainline/network-acceptance/nic-rx-payload-accept.py', ['--keep-affinity'])):
        result = subprocess.run([sys.executable, str(script), *extra], capture_output=True, text=True, timeout=120)
        (OUT / (name + '.json')).write_text(result.stdout)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        print(name + ' PASS', flush=True)


def main():
    baseline = json.loads((OUT / 'ipv4-stream-control/record.json').read_text())
    if baseline['result'] != 'PASS':
        raise RuntimeError('ordinary IPv4 direct-submit control did not pass')
    for version in (4, 6):
        if version == 6 and not case(6, 64, True):
            continue
        for payload in (64, 128, 145):
            passed = case(version, payload)
            if payload == 64 and not passed:
                print(f'IPv{version} template one-output gate failed; multi-output cases skipped', flush=True)
                break
    recover()


if __name__ == '__main__':
    main()
