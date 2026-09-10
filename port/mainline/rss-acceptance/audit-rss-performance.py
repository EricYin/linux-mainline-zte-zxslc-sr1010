#!/usr/bin/env python3
"""Audit controlled RSS TCP samples and summarize measured, per-interface effects."""
import argparse
from collections import defaultdict
import ipaddress
import json
from pathlib import Path
import re
import statistics


def bucket(connection):
    value = (int(ipaddress.ip_address(connection['local_host'])) ^
             int(ipaddress.ip_address(connection['remote_host'])) ^
             (connection['local_port'] << 16 | connection['remote_port']) ^ 6)
    for shift in (16, 8, 4, 3):
        value ^= value >> shift
    return value & 7


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', type=Path, nargs='+')
    parser.add_argument('--exclude-receiver-mismatch', type=Path, action='append', default=[])
    args = parser.parse_args()
    checks, groups, excluded = [], defaultdict(list), []
    exclusions = {p.resolve() for p in args.exclude_receiver_mismatch}

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    for directory in args.directories:
        for path in sorted(directory.glob('*.json')):
            if not re.fullmatch(r'(wan|lan)-(\d+|restore)\.json', path.name):
                continue
            d = json.loads(path.read_text())
            if 'iperf' not in d:
                if 'restored' in d:
                    check(str(path) + ' restoration', d['original'] == d['restored'] and
                          d['affinity_before'] == d['affinity_after'])
                continue
            name = str(path)
            start, end = d['iperf']['start'], d['iperf']['end']
            test = start['test_start']
            check(name + ' workload', test['protocol'] == 'TCP' and test['num_streams'] == 8 and
                  test['duration'] == 10 and test['omit'] == 2 and not test['reverse'])
            check(name + ' unchanged IRQ and RPS', d['affinity'] == d['affinity_after'] and
                  d['affinity'].split() == ['1', '2', '0', '0'])
            if 'server_start' in d:
                check(name + ' inherited server CPU0 affinity',
                      re.search(r'current affinity mask: 1\s*$', d['server_start']) is not None)
            delta = d['delta']
            check(name + ' driver health', all(delta[k] == 0 for k in
                  ('rx_errors', 'rx_dropped', 'rx_invalid_dma', 'rx_page_lookup_misses',
                   'rx_refill_shortfalls', 'tx_errors', 'tx_timeouts')))
            check(name + ' stable active hardware', d['before']['stats']['rx_hash_active'] == 1 and
                  d['after']['stats']['rx_hash_active'] == 1 and delta['rx_hash_loads'] == 0 and
                  delta['rx_hw_hash_packets'] > 1000)
            connections = start['connected']
            buckets = [bucket(c) for c in connections]
            check(name + ' eight tuple buckets', len(connections) == 8 and set(buckets) == set(range(8)))
            base = 8 if d['mode'] == 'wan' else 0
            expected = {base | (b & 3) | (((d['mask'] >> b) & 1) << 2) for b in buckets}
            counts = {q: delta[f'rx_hw_queue_{q}_descs'] for q in range(base, base + 8)}
            tolerance = max(100, sum(counts.values()) / 1000)
            check(name + ' hardware queue distribution', all(counts[q] > 1000 for q in expected) and
                  all(n <= tolerance for q, n in counts.items() if q not in expected))
            receiver = end['sum_received']
            server = d['iperf']['server_output_json']['end']['sum_received']
            if path.resolve() in exclusions:
                check(name + ' explicit counter-mismatch exclusion', receiver['bytes'] != server['bytes'])
                excluded.append({'path': str(path), 'client_received_bytes': receiver['bytes'],
                                 'server_received_bytes': server['bytes']})
                continue
            check(name + ' receiver accounting', abs(receiver['seconds'] - 10) < 0.2 and
                  receiver['bytes'] > 0 and receiver['bytes'] == server['bytes'])
            cpu = {}
            for core, before in d['before']['cpu'].items():
                ticks = [a - b for a, b in zip(d['after']['cpu'][core], before)]
                cpu[core] = 100 * (1 - (ticks[3] + ticks[4]) / sum(ticks))
                check(name + ' ' + core + ' CPU accounting', min(ticks) >= 0 and
                      abs(cpu[core] - d['cpu'][core]['busy_percent']) < 1e-9)
            groups[(d['mode'], d['mask'])].append({
                'gbps': receiver['bits_per_second'] / 1e9,
                'retransmits': end['sum_sent'].get('retransmits'),
                'cpu': cpu,
                'cpu_busy_sum_per_gbps': sum(cpu.values()) / (receiver['bits_per_second'] / 1e9),
            })
    check('all explicit exclusions found', len(excluded) == len(exclusions))
    summary = {}
    for (mode, mask), rows in sorted(groups.items()):
        check(f'{mode} {mask:02x} repeated samples', len(rows) >= 3)
        rates = [r['gbps'] for r in rows]
        summary.setdefault(mode, {})[f'{mask:02x}'] = {
            'samples': len(rows), 'mean_gbps': statistics.mean(rates),
            'median_gbps': statistics.median(rates), 'min_gbps': min(rates), 'max_gbps': max(rates),
            'mean_cpu_busy': {c: statistics.mean(r['cpu'][c] for r in rows) for c in ('cpu0', 'cpu1')},
            'mean_cpu_busy_sum_per_gbps': statistics.mean(r['cpu_busy_sum_per_gbps'] for r in rows),
            'retransmits': [r['retransmits'] for r in rows],
        }
    for mode, values in summary.items():
        check(mode + ' both single queues compared', all(k in values for k in ('00', 'ff', 'f0')))
        if all(k in values for k in ('00', 'ff', 'f0')):
            best = max(values[k]['mean_gbps'] for k in ('00', 'ff'))
            values['dual_vs_best_single_mean_percent'] = (values['f0']['mean_gbps'] / best - 1) * 100
            best = max(values[k]['median_gbps'] for k in ('00', 'ff'))
            values['dual_vs_best_single_median_percent'] = (values['f0']['median_gbps'] / best - 1) * 100
    print(json.dumps({'result': 'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL',
                      'summary': summary, 'excluded': excluded, 'checks': checks}, indent=2))
    return 0 if all(c['result'] == 'PASS' for c in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
