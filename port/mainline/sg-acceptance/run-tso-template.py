#!/usr/bin/env python3
"""Capture one retained template probe, then reload the unchanged RAM FIT."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import runpy
import struct
import subprocess
import time


ROOT = Path(__file__).resolve().parents[3]
BOARD = runpy.run_path(str(ROOT / 'port/mainline/network-acceptance/nic-rx-accept.py'))['board']
WIRE = runpy.run_path(str(Path(__file__).with_name('woe-wire-accept.py')))
PANE = 'sr1010-serial:0'
FIT_SHA = '43bef302fe172abb1c7def61cdebd319ef60fde29131d8c614af6000a9c7893d'


def tail():
    return subprocess.check_output(['tmux', 'capture-pane', '-pJt', PANE, '-S', '-45'], text=True).rstrip()


def send(command):
    subprocess.run(['tmux', 'send-keys', '-t', PANE, '-l', command], check=True)
    subprocess.run(['tmux', 'send-keys', '-t', PANE, 'Enter'], check=True)


def reboot():
    send('reboot -f')
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        screen = tail()
        if 'Hit any key' in screen or 'autoboot' in screen:
            subprocess.run(['tmux', 'send-keys', '-t', PANE, 'C-c'], check=True)
        if screen.endswith('=>'):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError('U-Boot prompt missing: ' + screen[-1200:])
    send('tftpboot sr1010-zxdbg.itb')
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        screen = tail()
        if screen.endswith('=>') and 'Bytes transferred = 11003260' in screen:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError('FIT transfer not verified: ' + screen[-1200:])
    send('bootm')
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        screen = tail()
        if 'sr1010-zxdbg#' in screen and 'Link is Up - 2.5Gbps' in screen:
            return screen
        time.sleep(0.3)
    raise RuntimeError('RAM Linux did not become ready: ' + screen[-1600:])


def inspect(frame):
    version = frame[14] >> 4
    ip = frame[14:]
    if version == 4:
        iplen = (ip[0] & 15) * 4
        length = struct.unpack_from('!H', ip, 2)[0]
        tcp = ip[iplen:length]
        pseudo = ip[12:20] + struct.pack('!BBH', 0, 6, len(tcp))
        ip_ok = WIRE['checksum'](ip[:iplen]) == 0
        ident = struct.unpack_from('!H', ip, 4)[0]
    else:
        iplen = 40
        length = 40 + struct.unpack_from('!H', ip, 4)[0]
        tcp = ip[40:length]
        pseudo = ip[8:40] + struct.pack('!I3xB', len(tcp), 6)
        ip_ok, ident = True, None
    header = (tcp[12] >> 4) * 4
    return {'version': version, 'wire_bytes': len(frame), 'ip_bytes': length,
            'payload_bytes': len(tcp) - header,
            'sequence': struct.unpack_from('!I', tcp, 4)[0],
            'ack': struct.unpack_from('!I', tcp, 8)[0], 'flags': tcp[13],
            'ipv4_id': ident, 'ip_checksum_ok': ip_ok,
            'tcp_checksum_ok': WIRE['checksum'](pseudo + tcp) == 0,
            'zero_payload': not any(tcp[header:]), 'payload': tcp[header:].hex(), 'frame': frame.hex()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', type=int, choices=(4, 6), default=4)
    parser.add_argument('--payload', type=int, choices=(64, 128, 145), default=64)
    parser.add_argument('--ordinary', action='store_true')
    parser.add_argument('--pattern', action='store_true')
    parser.add_argument('--trace-steps', type=int, default=0)
    parser.add_argument('--trace-pc', type=int, choices=(1924, 2053, 2054, 2058), default=1924)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    fit = (ROOT / 'out/sr1010-zxdbg.itb').read_bytes()
    if hashlib.sha256(fit).hexdigest() != FIT_SHA:
        raise RuntimeError('recovery FIT changed')
    report = {'version': args.version, 'payload': args.payload, 'template': not args.ordinary,
              'pattern': args.pattern,
              'module_sha256': hashlib.sha256((ROOT / 'out/tso-template-probe.ko').read_bytes()).hexdigest()}
    report['prepare'] = BOARD('tftp -g -r tso-template-probe.ko -l /tmp/tso-template-probe.ko 192.168.1.100; '
                               'cat /sys/class/net/eth0/address; cat /proc/sys/kernel/tainted')
    mac = re.search(r'(?m)^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$', report['prepare'])[0]
    capture = subprocess.Popen(['sudo', '-n', 'tcpdump', '--immediate-mode', '-U', '-n', '-i', 'en8', '-w',
                                str(args.directory / 'wire.pcap'),
                                f'ether src {mac}'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        ready = capture.stderr.readline()
        report['capture_ready'] = ready
        if 'listening on' not in ready:
            raise RuntimeError(ready)
        time.sleep(0.2)
        calibration = subprocess.run(['ping', '-c', '1', '-S', '192.168.1.100', '192.168.1.1'],
                                     capture_output=True, text=True, timeout=5)
        report['calibration_ping'] = calibration.stdout
        if calibration.returncode:
            raise RuntimeError('capture calibration peer not reachable')
        pattern_arg = ' pattern=1' if args.pattern else ''
        if args.trace_steps:
            pattern_arg += f' trace_steps={args.trace_steps} trace_pc={args.trace_pc}'
        report['probe'] = BOARD(f'insmod /tmp/tso-template-probe.ko arm=1 template={int(not args.ordinary)} '
                                f'version={args.version} payload={args.payload}{pattern_arg}; echo PROBE_EXIT=$?; '
                                'dmesg | grep tso-template:')
        (args.directory / 'record.json').write_text(json.dumps(report, indent=2) + '\n')
        if args.trace_steps:
            match = re.search(r'steps=(\d+) result=(-?\d+) stopped=([0-9a-f]+) busy=([0-9a-f]+) '
                              r'last_pc=(\d+) record_size=(\d+)', report['probe'])
            if match:
                total = int(match[1]) * int(match[6])
                raw = bytearray()
                for offset in range(0, total, 4096):
                    expected = min(4096, total - offset)
                    for attempt in range(3):
                        output = BOARD('dd if=/sys/kernel/debug/tso-template/trace bs=4096 '
                                       f'skip={offset // 4096} count=1 2>/dev/null | base64')
                        encoded = ''.join(line.strip() for line in output.splitlines()
                                          if re.fullmatch(r'[A-Za-z0-9+/]+={0,2}', line.strip()))
                        try:
                            chunk = base64.b64decode(encoded, validate=True)
                        except ValueError:
                            continue
                        if len(chunk) == expected:
                            raw.extend(chunk)
                            break
                    else:
                        raise RuntimeError(f'incomplete serial trace chunk at {offset}')
                size = int(match[6])
                if len(raw) != total or size not in (536, 544):
                    raise RuntimeError('trace size does not match recorded binary layout')
                (args.directory / 'trace.bin').write_bytes(raw)
                rows = []
                for offset in range(0, total, size):
                    fields = struct.unpack_from('<IIIIQ128I' if size == 536 else '<IIIIIIQ128I', raw, offset)
                    base = 5 if size == 536 else 7
                    rows.append({'step': fields[0], 'pc': fields[1], 'flags': fields[2],
                                 'done': fields[3], 'me': None if size == 536 else fields[4],
                                 'instruction': f'{fields[base - 1]:016x}',
                                 'packet': fields[base:base + 64], 'spr': fields[base + 64:base + 96],
                                 'response': fields[base + 96:base + 128]})
                report['trace'] = rows
                report['trace_summary'] = {'steps': int(match[1]), 'result': int(match[2]),
                                           'stopped': int(match[3], 16), 'busy': int(match[4], 16),
                                           'last_pc': int(match[5]), 'requested': args.trace_steps}
                (args.directory / 'record.json').write_text(json.dumps(report, indent=2) + '\n')
                print(f'trace collected: {len(rows)} instructions', flush=True)
        time.sleep(1)
    finally:
        try:
            children = subprocess.run(['pgrep', '-P', str(capture.pid)],
                                      capture_output=True, text=True).stdout.split()
            subprocess.run(['sudo', '-n', 'kill', '-TERM', *(children or [str(capture.pid)])], check=True)
            _, error = capture.communicate(timeout=5)
            report['capture_end'] = error
        finally:
            (args.directory / 'record.json').write_text(json.dumps(report, indent=2) + '\n')
            print('reloading unchanged RAM FIT', flush=True)
            report['reboot'] = reboot()
            (args.directory / 'record.json').write_text(json.dumps(report, indent=2) + '\n')
    all_packets = list(WIRE['pcap_packets']((args.directory / 'wire.pcap').read_bytes()))
    report['all_wire_frames'] = [frame.hex() for frame, _ in all_packets]
    packets = []
    for frame, original in all_packets:
        if len(frame) < 54 or frame[12:14] not in (b'\x08\x00', b'\x86\xdd'):
            continue
        transport = 14 + ((frame[14] & 15) * 4 if frame[12:14] == b'\x08\x00' else 40)
        if frame[transport:transport + 4] == struct.pack('!HH', 49041, 49042):
            packets.append((frame, original))
    report['frames'] = [inspect(frame) for frame, original in packets]
    driver_control = None
    if 'driver_control_begin=1' in report['probe'] and report['frames']:
        driver_control = report['frames'].pop(0)
        packets.pop(0)
    report['driver_control'] = driver_control
    block_a = bytes(0x31 + (i & 15) for i in range(64)).hex()
    block_b = bytes(0x71 + (i & 15) for i in range(64)).hex()
    if args.pattern:
        actual = [f['payload'] for f in report['frames']]
        report['payload_behavior'] = ('advancing' if actual == [block_a, block_b] else
                                      'repeated-first' if actual == [block_a, block_a] else 'other')
    count = (args.payload + 63) // 64
    sizes = [64] * (count - 1) + [args.payload - 64 * (count - 1)]
    report['checks'] = {
        'submitted': 'PROBE_EXIT=0' in report['probe'] and 'retained=1' in report['probe'],
        'packet_count': len(packets) == count,
        'payload_sizes': [f['payload_bytes'] for f in report['frames']] == sizes,
        'sequence': [f['sequence'] for f in report['frames']] == [0x10203040 + 64 * i for i in range(count)],
        'checksums': bool(packets) and all(f['ip_checksum_ok'] and f['tcp_checksum_ok'] for f in report['frames']),
        'payload_integrity': bool(packets) and (report['payload_behavior'] != 'other' if args.pattern else
                                               all(f['zero_payload'] for f in report['frames'])),
        'capture_no_drops': '0 packets dropped by kernel' in report['capture_end'],
        'capture_calibrated': bool(all_packets) and 'ttl=' in report['calibration_ping'].lower(),
        'driver_control': driver_control is not None and driver_control['payload_bytes'] == 64 and
                          driver_control['ip_checksum_ok'] and driver_control['tcp_checksum_ok'],
    }
    report['wire_result'] = 'PASS' if all(report['checks'].values()) else 'FAIL'
    if args.trace_steps:
        summary = report.get('trace_summary', {})
        report['checks']['trace_complete'] = (summary.get('result') == 0 and
            0 < summary.get('steps', 0) < args.trace_steps and summary.get('stopped') == 0)
    report['result'] = 'PASS' if all(report['checks'].values()) else 'FAIL'
    (args.directory / 'record.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'result': report['result'], 'checks': report['checks'], 'frames': report['frames']}), flush=True)


if __name__ == '__main__':
    main()
