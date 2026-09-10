#!/usr/bin/env python3
"""Controlled TCP/UDP receive comparisons with unchanged IRQs and restored RSS maps."""
import argparse
import base64
import json
from pathlib import Path
import re
import runpy
import subprocess
import time


HERE = Path(__file__).resolve().parent
HELPERS = runpy.run_path(str(HERE / 'run-rx-hash-export.py'))
BOARD, STATS = HELPERS['BOARD'], HELPERS['STATS']


def table(dev):
    output = BOARD(f'/tmp/ethtool-test -x {dev}')
    match = re.search(r'0:\s+((?:[01]\s+){7}[01])', output)
    if not match:
        raise RuntimeError(output)
    return sum(int(v) << i for i, v in enumerate(match[1].split()))


def snapshot():
    output = BOARD('cat /proc/stat | head -3; cat /proc/uptime; cat /proc/net/snmp; '
                   '/tmp/ethtool-test -S eth0')
    cpus = {m[1]: list(map(int, m[2].split()))[:8] for m in
            re.finditer(r'(?m)^(cpu[01]) (.*)$', output)}
    snmp = {}
    for protocol in ('Ip', 'Udp'):
        rows = re.findall(r'(?m)^' + protocol + r': (.*)$', output)
        if len(rows) == 2:
            snmp[protocol] = dict(zip(rows[0].split(), map(int, rows[1].split())))
    return {'cpu': cpus, 'stats': STATS(output), 'snmp': snmp}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('wan', 'lan'), required=True)
    parser.add_argument('--seconds', type=int, default=10)
    parser.add_argument('--trials', type=int, default=6)
    parser.add_argument('--source-base', type=int, default=48000)
    parser.add_argument('--masks', help='explicit comma-separated 0, 255 or 240 map sequence')
    parser.add_argument('--udp-pps', type=int, help='aggregate requested UDP packets/s across eight streams')
    parser.add_argument('--payload', type=int, default=64, help='UDP payload bytes')
    parser.add_argument('--server-mask', type=lambda value: int(value, 0), choices=(1, 3),
                        help='optional affinity for this temporary iperf server only')
    parser.add_argument('--socket-buffer', type=int, help='per-test iperf socket buffer request in bytes')
    parser.add_argument('--xdp-sink', action='store_true', help='WAN UDP data-only XDP sink, preserving control traffic')
    parser.add_argument('--observe-order', action='store_true', help='observe minimum-frame UDP sequence numbers through a packet socket')
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    sequence = (0, 0xf0, 0xf0, 0, 0, 0xf0)[:args.trials]
    if args.masks:
        sequence = tuple(int(v, 0) for v in args.masks.split(','))
        args.trials = len(sequence)
    if (not 3 <= args.seconds <= 30 or not 1 <= args.trials <= 12 or
            (not args.masks and args.trials > 6) or
            any(v not in (0, 0xff, 0xf0) for v in sequence)):
        parser.error('seconds must be 3..30 with 1..12 masks chosen from 0, 255, 240')
    if not 1 <= args.source_base <= 65535 - 32 * (args.trials - 1) - 7 or args.source_base % 8:
        parser.error('source port range must fit port limits and start on an eight-port boundary')
    if args.udp_pps is not None and (not 1 <= args.udp_pps <= 2000000 or not 16 <= args.payload <= 1472):
        parser.error('UDP rate must be 1..2000000 packets/s and payload 16..1472 bytes')
    if args.socket_buffer is not None and not 1024 <= args.socket_buffer <= 4194304:
        parser.error('socket buffer must be 1024..4194304 bytes')
    if args.xdp_sink and (args.mode != 'wan' or not args.udp_pps):
        parser.error('XDP sink requires WAN UDP')
    if args.observe_order and (not args.udp_pps or args.payload != 18 or args.xdp_sink):
        parser.error('order observer requires 18-byte UDP payload and no XDP sink')
    args.directory.mkdir(parents=True, exist_ok=True)
    dev = 'eth0' if args.mode == 'wan' else 'lan-cpu0'
    network = 1 if args.mode == 'wan' else 5
    original = table(dev)
    affinity_cmd = ('cat /proc/irq/22/smp_affinity /proc/irq/25/smp_affinity; '
                    f'cat /sys/class/net/{dev}/queues/rx-0/rps_cpus '
                    f'/sys/class/net/{dev}/queues/rx-1/rps_cpus')
    affinity = BOARD(affinity_cmd)
    try:
        for index, mask in enumerate(sequence):
            setter = BOARD(f'/tmp/rss-set {dev} {mask} > /tmp/rss-perf-set.txt 2>&1; '
                           'sleep 7; cat /tmp/rss-perf-set.txt')
            if 'errno=0 result=PASS' not in setter or table(dev) != mask:
                raise RuntimeError(setter)
            peer = (['ping', '-c', '1', '-S', '192.168.1.100', '192.168.1.1'] if args.mode == 'wan'
                    else ['ssh', '-o', 'BatchMode=yes', 'cnjn@192.168.3.43',
                          'ping', '-n', '1', '-w', '2000', '192.168.5.1'])
            check = subprocess.run(peer, capture_output=True, timeout=6)
            if check.returncode or b'ttl=' not in check.stdout.lower():
                raise RuntimeError('peer not ready before performance sample')
            sink_start = None
            if args.xdp_sink:
                sink_start = BOARD(f'/tmp/xdp-pps-sink eth0 {args.payload} > /tmp/rss-pps-sink.txt 2>&1 '
                                    '& rss_pps_sink=$!; sleep 0.2; cat /tmp/rss-pps-sink.txt')
                if 'XDP_PPS_SINK_READY' not in sink_start:
                    raise RuntimeError(sink_start)
            launch = '' if args.server_mask is None else f'taskset {args.server_mask} '
            ready = BOARD(launch + 'iperf3 -s -1 -p 5203 -J > /tmp/rss-perf-server.json 2>&1 '
                          '& rss_perf=$!; sleep 0.2; kill -0 $rss_perf; echo SERVER=$?; '
                          'taskset -p $rss_perf')
            if 'SERVER=0' not in ready:
                raise RuntimeError(ready)
            order_start = None
            if args.observe_order:
                order_dev = 'eth0' if args.mode == 'wan' else 'lan1'
                order_start = BOARD(f'taskset 3 /tmp/udp-order-observe {order_dev} '
                                    f'{args.source_base + index * 32} > /tmp/rss-order.txt 2>&1 '
                                    '& rss_order=$!; sleep 0.2; head -1 /tmp/rss-order.txt')
                if 'UDP_ORDER_READY' not in order_start:
                    raise RuntimeError(order_start)
            before = snapshot()
            command = (['iperf3'] if args.mode == 'wan' else
                       ['ssh', '-o', 'BatchMode=yes', 'cnjn@192.168.3.43', 'iperf3'])
            command += ['-c', f'192.168.{network}.1', '-B', f'192.168.{network}.100',
                        '-p', '5203', '--cport', str(args.source_base + index * 32), '-P', '8',
                        '-t', str(args.seconds), '-O', '2', '-J', '--get-server-output']
            if args.udp_pps is not None:
                command += ['-u', '-l', str(args.payload), '-b', str(args.udp_pps * args.payload)]
            if args.socket_buffer is not None:
                command += ['-w', str(args.socket_buffer)]
            begin = time.monotonic()
            try:
                result = subprocess.run(command, capture_output=True, text=True,
                                        timeout=args.seconds + 25)
            except subprocess.TimeoutExpired as error:
                failed = {'command': command, 'timeout_seconds': args.seconds + 25,
                          'stdout': (error.stdout or b'').decode(errors='replace'),
                          'stderr': (error.stderr or b'').decode(errors='replace'),
                          'before': before, 'after': snapshot(),
                          'server': BOARD('cat /tmp/rss-perf-server.json')}
                if args.observe_order:
                    failed['observer'] = BOARD('kill -TERM $rss_order; wait $rss_order; '
                                               'unset rss_order; cat /tmp/rss-order.txt')
                if args.mode == 'lan':
                    # Terminating local SSH alone leaves its remote client alive.
                    port = args.source_base + index * 32
                    cleanup = ('Get-CimInstance Win32_Process -Filter "Name=\'iperf3.exe\'" | '
                               f'Where-Object {{ $_.CommandLine -match "--cport {port}( |$)" }} | '
                               'ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }')
                    stopped = subprocess.run(['ssh', '-o', 'BatchMode=yes', 'cnjn@192.168.3.43',
                                              'powershell.exe', '-NoProfile', '-EncodedCommand',
                                              base64.b64encode(cleanup.encode('utf-16le')).decode()],
                                             capture_output=True, timeout=15)
                    failed['remote_cleanup'] = {'returncode': stopped.returncode,
                                               'stdout': stopped.stdout.decode(errors='replace')}
                (args.directory / f'{args.mode}-{index}-error.json').write_text(
                    json.dumps(failed, indent=2) + '\n')
                raise
            elapsed = time.monotonic() - begin
            after = snapshot()
            order_stop = None
            if args.observe_order:
                order_stop = BOARD('kill -TERM $rss_order; wait $rss_order; '
                                   'unset rss_order; cat /tmp/rss-order.txt')
            sink_stop = None
            if args.xdp_sink:
                sink_stop = BOARD('kill -TERM $rss_pps_sink; wait $rss_pps_sink; '
                                  'unset rss_pps_sink; cat /tmp/rss-pps-sink.txt')
            BOARD('wait $rss_perf; unset rss_perf')
            if result.returncode:
                (args.directory / f'{args.mode}-{index}-error.json').write_text(json.dumps(
                    {'command': command, 'stdout': result.stdout, 'stderr': result.stderr,
                     'returncode': result.returncode, 'before': before, 'after': after}, indent=2) + '\n')
                raise RuntimeError(result.stderr or result.stdout)
            iperf = json.loads(result.stdout)
            if 'error' in iperf:
                raise RuntimeError(iperf['error'])
            delta = {k: after['stats'][k] - v for k, v in before['stats'].items()}
            snmp_delta = {protocol: {k: after['snmp'][protocol][k] - v for k, v in values.items()}
                          for protocol, values in before['snmp'].items()}
            cpu = {}
            for name, values in before['cpu'].items():
                ticks = [a - b for a, b in zip(after['cpu'][name], values)]
                cpu[name] = {'ticks': ticks,
                             'busy_percent': 100 * (1 - (ticks[3] + ticks[4]) / sum(ticks)),
                             'softirq_percent': 100 * ticks[6] / sum(ticks)}
            record = {'mode': args.mode, 'index': index, 'mask': mask,
                      'requested_pps': args.udp_pps, 'udp_payload': args.payload if args.udp_pps else None,
                      'server_mask': args.server_mask,
                      'socket_buffer': args.socket_buffer,
                      'xdp_sink': args.xdp_sink, 'sink_start': sink_start, 'sink_stop': sink_stop,
                      'order_start': order_start, 'order_stop': order_stop,
                      'command': command, 'setter': setter, 'before': before,
                      'after': after, 'delta': delta, 'cpu': cpu, 'iperf': iperf,
                      'snmp_delta': snmp_delta,
                      'wall_seconds': elapsed, 'affinity': affinity,
                      'server_start': ready,
                      'affinity_after': BOARD(affinity_cmd)}
            path = args.directory / f'{args.mode}-{index}.json'
            path.write_text(json.dumps(record, indent=2) + '\n')
            print(json.dumps({'trial': index, 'mask': mask, 'record': str(path),
                              'received_bps': iperf['end']['sum_received']['bits_per_second'],
                              'cpu': cpu}), flush=True)
    finally:
        restored = BOARD('if [ -n "$rss_order" ]; then kill -TERM $rss_order; '
                         'wait $rss_order; unset rss_order; fi; '
                         'if [ -n "$rss_perf" ]; then kill $rss_perf 2>/dev/null; '
                         'wait $rss_perf; unset rss_perf; fi; '
                         'if [ -n "$rss_pps_sink" ]; then kill -TERM $rss_pps_sink; '
                         'wait $rss_pps_sink; unset rss_pps_sink; fi; '
                         f'/tmp/rss-set {dev} {original}; sleep 7')
        (args.directory / f'{args.mode}-restore.json').write_text(json.dumps(
            {'original': original, 'restored': table(dev), 'output': restored,
             'affinity_before': affinity, 'affinity_after': BOARD(affinity_cmd)}, indent=2) + '\n')


if __name__ == '__main__':
    main()
