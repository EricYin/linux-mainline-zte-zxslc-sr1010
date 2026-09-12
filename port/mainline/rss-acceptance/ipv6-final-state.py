#!/usr/bin/env python3
"""Record default IPv6 RSS boot, regression results and restored runtime."""
import hashlib
import json
from pathlib import Path
import re
import runpy


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'out/acceptance/rss/ipv6-continuous'
HELPERS = runpy.run_path(str(Path(__file__).with_name('run-ipv6-rss.py')))
BOARD, READ_STATS = HELPERS['BOARD'], HELPERS['read_stats']


def main():
    boot = json.loads((OUT / 'default-boot.json').read_text())
    udp = json.loads((OUT / 'default-udp4.json').read_text())
    tcp = json.loads((OUT / 'default-tcp4.json').read_text())
    smoke = {f'{mode}-{kind}': json.loads((OUT / f'default-{mode}-{kind}.json').read_text())
             for mode in ('wan', 'lan') for kind in ('ipv6-udp', 'ipv6-tcp', 'ipv6-ack')}
    state = {
        'cmdline': BOARD('cat /proc/cmdline'),
        'ipv6_parameter': BOARD('cat /sys/module/zx279133/parameters/rx_hash_ipv6').strip(),
        'taint': BOARD('cat /proc/sys/kernel/tainted').strip(),
        'modules': BOARD('cat /proc/modules'),
        'affinity': BOARD('cat /proc/irq/22/smp_affinity /proc/irq/25/smp_affinity '
                          '/sys/class/net/eth0/queues/rx-*/rps_cpus '
                          '/sys/class/net/lan-cpu0/queues/rx-*/rps_cpus').split(),
        'maps': BOARD('/tmp/ethtool-test -x eth0; /tmp/ethtool-test -x lan-cpu0'),
        'ipv6_fields': BOARD('/tmp/ethtool-test -n eth0 rx-flow-hash udp6; '
                             '/tmp/ethtool-test -n lan-cpu0 rx-flow-hash tcp6'),
        'features': BOARD('/tmp/ethtool-test -k eth0'),
        'stats': READ_STATS(),
        'faults': BOARD('dmesg | grep -E "BUG:|WARNING:|Oops:|Call trace:|NETDEV WATCHDOG|Kernel panic"'),
        'firmware': BOARD('sha256sum /lib/firmware/zte/zx279133/mcode_intel.bin'),
        'processes': BOARD('ps'),
    }
    features = dict(re.findall(r'(?m)^\s*([a-z0-9-]+): (on|off)', state['features']))
    stats = state['stats']
    blob = (ROOT / 'out/sr1010-zxdbg.itb').read_bytes()
    checks = {
        'default_ipv6_enabled': state['ipv6_parameter'] == 'Y' and 'zx279133.rx_hash' not in state['cmdline'],
        'default_ipv6_packets': all(record['result'] == 'PASS' for record in smoke.values()),
        'ipv4_udp_regression': udp['result'] == 'PASS',
        'ipv4_tcp_regression': tcp['result'] == 'PASS' and tcp['affinity_unchanged'],
        'untainted': state['taint'] == '0',
        'normal_switch': 'zx279133_rtl8372n ' in state['modules'] and 'tag_zx279133_rtl8372n ' in state['modules'],
        'no_probe_modules': not any(name in state['modules'] for name in ('rx_ppu', 'tso_template', 'woe_')),
        'no_test_processes': not any('/tmp/' + name in state['processes'] for name in
                                     ('rx-raw-verify', 'rx-hash-observe', 'nic-xdp-pass', 'xsk-zc',
                                      'nic-rx-stream', 'rss-udp-sequence')),
        'irq_and_rps_restored': state['affinity'] == ['1', '2', '0', '0', '0', '0'],
        'both_default_maps': len(re.findall(r'0:\s+0\s+0\s+0\s+0\s+1\s+1\s+1\s+1', state['maps'])) == 2,
        'ipv6_field_queries': state['ipv6_fields'].count('IP SA') == 2 and state['ipv6_fields'].count('IP DA') == 2,
        'features': features.get('receive-hashing') == 'on' and all(features.get(key) == 'off' for key in
                     ('scatter-gather', 'tcp-segmentation-offload', 'tx-tcp-segmentation', 'tx-tcp6-segmentation')),
        'hash_loaded_once': stats['rx_hash_active'] == stats['rx_hash_loads'] == 1,
        'no_pending_or_errors': all(stats[key] == 0 for key in
                                   ('tx_pending', 'tx_timeouts', 'rx_errors', 'tx_errors', 'rx_refill_deficit')),
        'no_faults': not state['faults'].strip(),
        'same_booted_fit': len(blob) == boot['bytes'] and hashlib.sha256(blob).hexdigest() == boot['sha256'],
        'firmware_unchanged': state['firmware'].startswith(
            '056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40'),
    }
    result = 'PASS' if all(checks.values()) else 'FAIL'
    report = {'result': result, 'checks': checks, 'fit_sha256': boot['sha256'], 'fit_bytes': boot['bytes'],
              'udp4': udp['receiver'], 'tcp4': tcp['receiver_rows'], 'state': state}
    (OUT / 'final-state.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'result': result, 'checks': checks}))
    return 0 if result == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
