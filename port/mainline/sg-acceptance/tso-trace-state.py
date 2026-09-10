#!/usr/bin/env python3
"""Record restored runtime after the template trace and network regressions."""
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'out/acceptance/tso-trace'
BOARD = runpy.run_path(str(ROOT / 'port/mainline/network-acceptance/nic-rx-accept.py'))['board']


def main():
    udp = json.loads((OUT / 'udp-final.json').read_text())
    tcp = json.loads((OUT / 'tcp-final.json').read_text())
    state = {
        'taint': BOARD('cat /proc/sys/kernel/tainted').strip(),
        'modules': BOARD('cat /proc/modules'),
        'affinity': BOARD('cat /proc/irq/22/smp_affinity /proc/irq/25/smp_affinity '
                          '/sys/class/net/eth0/queues/rx-*/rps_cpus '
                          '/sys/class/net/lan-cpu0/queues/rx-*/rps_cpus').split(),
        'maps': BOARD('/tmp/ethtool-test -x eth0; /tmp/ethtool-test -x lan-cpu0'),
        'features': BOARD('/tmp/ethtool-test -k eth0'),
        'stats': BOARD('/tmp/ethtool-test -S eth0'),
        'faults': BOARD('dmesg | grep -E "BUG:|WARNING:|Oops:|Call trace:|timed out|NETDEV WATCHDOG"'),
        'firmware': BOARD('sha256sum /lib/firmware/zte/zx279133/mcode_intel.bin'),
        'capture_processes': [line.strip() for line in subprocess.run(
            ['ps', '-axo', 'pid=,comm=,args='], capture_output=True, text=True).stdout.splitlines()
            if len(line.split(None, 2)) == 3 and Path(line.split(None, 2)[1]).name == 'tcpdump'],
    }
    stats = {key: int(value) for key, value in re.findall(r'(?m)^\s+([a-z0-9_]+): (\d+)$', state['stats'])}
    features = dict(re.findall(r'(?m)^\s*([a-z0-9-]+): (on|off)', state['features']))
    fit = (ROOT / 'out/sr1010-zxdbg.itb').read_bytes()
    checks = {
        'udp_regression': udp['result'] == 'PASS',
        'tcp_regression': tcp['result'] == 'PASS' and tcp['affinity_unchanged'],
        'untainted': state['taint'] == '0',
        'normal_switch_modules': 'zx279133_rtl8372n ' in state['modules'] and
                                 'tag_zx279133_rtl8372n ' in state['modules'],
        'probe_unloaded': 'tso_template' not in state['modules'],
        'irq_and_rps': state['affinity'] == ['1', '2', '0', '0', '0', '0'],
        'rss_maps': len(re.findall(r'0:\s+0\s+0\s+0\s+0\s+1\s+1\s+1\s+1', state['maps'])) == 2,
        'features': features.get('receive-hashing') == 'on' and all(
            features.get(key) == 'off' for key in ('scatter-gather', 'tcp-segmentation-offload',
                                                  'tx-tcp-segmentation', 'tx-tcp6-segmentation')),
        'hash_active': stats.get('rx_hash_active') == stats.get('rx_hash_loads') == 1,
        'no_pending_or_errors': all(stats.get(key) == 0 for key in
                                    ('tx_pending', 'tx_timeouts', 'rx_errors', 'tx_errors', 'rx_refill_deficit')),
        'no_faults': not state['faults'].strip(),
        'no_task_capture_processes': not any('tso-trace/' in line for line in state['capture_processes']),
        'firmware_unchanged': state['firmware'].startswith(
            '056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40'),
        'fit_unchanged': len(fit) == 11003260 and hashlib.sha256(fit).hexdigest() ==
                         '43bef302fe172abb1c7def61cdebd319ef60fde29131d8c614af6000a9c7893d',
    }
    report = {'result': 'PASS' if all(checks.values()) else 'FAIL', 'checks': checks,
              'udp': udp['receiver'], 'tcp_streams': tcp['receiver_rows'], 'state': state}
    (OUT / 'final-state.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'result': report['result'], 'checks': checks}))
    if report['result'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
