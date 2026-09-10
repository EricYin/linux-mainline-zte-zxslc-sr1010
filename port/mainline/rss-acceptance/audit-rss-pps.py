#!/usr/bin/env python3
"""Audit UDP offered/delivered PPS separately from sequence loss and socket drops."""
import argparse
from collections import defaultdict
import ipaddress
import json
from pathlib import Path
import re
import statistics


def udp_bucket(connection):
    value = (int(ipaddress.ip_address(connection['local_host'])) ^
             int(ipaddress.ip_address(connection['remote_host'])) ^
             (connection['local_port'] << 16 | connection['remote_port']) ^ 17)
    for shift in (16, 8, 4, 3):
        value ^= value >> shift
    return value & 7


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', type=Path, nargs='+')
    args = parser.parse_args()
    checks, groups = [], defaultdict(list)

    def check(name, passed):
        checks.append({'check': name, 'result': 'PASS' if passed else 'FAIL'})

    for directory in args.directories:
        for path in sorted(directory.glob('*.json')):
            if not re.fullmatch(r'(wan|lan)-(\d+|restore)\.json', path.name):
                continue
            d = json.loads(path.read_text())
            if 'restored' in d:
                check(str(path) + ' restored', d['original'] == d['restored'] and
                      d['affinity_before'] == d['affinity_after'])
                continue
            name = str(path)
            iperf = d['iperf']
            test = iperf['start']['test_start']
            payload = d['udp_payload']
            sender = iperf['end']['sum_sent']
            receiver = iperf['server_output_json']['end']['sum_received']
            offered = sender['packets'] / sender['seconds']
            delivered = receiver['bytes'] / payload / receiver['seconds']
            server_mask = d.get('server_mask') or 1
            socket_buffer = d.get('socket_buffer') or 0
            check(name + ' fixed UDP workload', test['protocol'] == 'UDP' and
                  test['num_streams'] == 8 and test['blksize'] == payload and
                  test['duration'] == 10 and test['omit'] == 2)
            check(name + ' sender reached requested rate', abs(offered / d['requested_pps'] - 1) < 0.05)
            check(name + ' requested socket buffers', iperf['start']['sock_bufsize'] == socket_buffer and
                  (not socket_buffer or
                   iperf['server_output_json']['start']['rcvbuf_actual'] >= socket_buffer))
            check(name + ' affinities unchanged', d['affinity'] == d['affinity_after'] and
                  d['affinity'].split() == ['1', '2', '0', '0'])
            check(name + ' recorded server affinity',
                  re.search(r'current affinity mask: ' + str(server_mask) + r'\s*$', d['server_start']) is not None)
            check(name + ' receiver datagram accounting', receiver['bytes'] % payload == 0 and
                  receiver['bytes'] // payload == receiver['packets'] - receiver['lost_packets'])
            check(name + ' receiver report agreement', all(receiver[k] == iperf['end']['sum_received'][k]
                  for k in ('bytes', 'packets', 'lost_packets')))
            check(name + ' no observed per-flow reordering', all(
                  stream['udp']['out_of_order'] == 0
                  for stream in iperf['server_output_json']['end']['streams']))
            delta = d['delta']
            check(name + ' no reported driver failures', all(delta[k] == 0 for k in
                  ('rx_errors', 'rx_dropped', 'rx_invalid_dma', 'rx_page_lookup_misses',
                   'rx_refill_shortfalls', 'tx_errors', 'tx_timeouts')))
            check(name + ' hash remains active', d['before']['stats']['rx_hash_active'] == 1 and
                  d['after']['stats']['rx_hash_active'] == 1 and delta['rx_hash_loads'] == 0)
            buckets = [udp_bucket(c) for c in iperf['start']['connected']]
            check(name + ' eight tuple buckets', len(buckets) == 8 and set(buckets) == set(range(8)))
            base = 8 if d['mode'] == 'wan' else 0
            expected = {base | (b & 3) | (((d['mask'] >> b) & 1) << 2) for b in buckets}
            counts = {q: delta[f'rx_hw_queue_{q}_descs'] for q in range(base, base + 8)}
            check(name + ' hardware queue distribution', all(counts[q] > 1000 for q in expected) and
                  all(n <= max(100, sum(counts.values()) / 1000) for q, n in counts.items() if q not in expected))
            udp = d['snmp_delta']['Udp']
            check(name + ' UDP counter classification', udp['InErrors'] == udp['RcvbufErrors'] and
                  udp['InCsumErrors'] == 0 and udp['MemErrors'] == 0)
            groups[(d['mode'], server_mask, socket_buffer, payload, d['requested_pps'], d['mask'])].append({
                'offered': offered, 'delivered': delivered, 'loss': receiver['lost_percent'],
                'lost_packets': receiver['lost_packets'], 'rcvbuf_errors': udp['RcvbufErrors'],
                'cpu': {c: v['busy_percent'] for c, v in d['cpu'].items()},
            })
    summary = []
    for (mode, server_mask, socket_buffer, payload, target, mask), rows in sorted(groups.items()):
        check(f'{mode} server {server_mask} buffer {socket_buffer} payload {payload} rate {target} map {mask} repeated samples', len(rows) >= 3)
        summary.append({'mode': mode, 'server_mask': server_mask, 'requested_pps': target,
                        'socket_buffer': socket_buffer,
                        'udp_payload': payload, 'ethernet_bytes_including_fcs': max(64, payload + 46),
                        'rss_mask': mask, 'samples': len(rows),
                        'offered_pps_median': statistics.median(r['offered'] for r in rows),
                        'delivered_pps_median': statistics.median(r['delivered'] for r in rows),
                        'loss_percent': [r['loss'] for r in rows],
                        'rcvbuf_errors': [r['rcvbuf_errors'] for r in rows],
                        'cpu_busy_mean': {c: statistics.mean(r['cpu'][c] for r in rows)
                                          for c in ('cpu0', 'cpu1')}})
    print(json.dumps({'result': 'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL',
                      'summary': summary, 'checks': checks}, indent=2))
    return 0 if all(c['result'] == 'PASS' for c in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
