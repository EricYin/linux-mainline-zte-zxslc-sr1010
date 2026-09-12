#!/usr/bin/env python3
"""Boot a named TFTP FIT in RAM and select the RSS implementation for testing."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[3]
PANE = 'sr1010-serial:0'


def screen():
    return subprocess.check_output(['tmux', 'capture-pane', '-pJt', PANE, '-S', '-45'], text=True).rstrip()


def send(command):
    subprocess.run(['tmux', 'send-keys', '-t', PANE, '-l', command], check=True)
    subprocess.run(['tmux', 'send-keys', '-t', PANE, 'Enter'], check=True)


def wait(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        text = screen()
        if predicate(text):
            return text
        time.sleep(0.2)
    raise RuntimeError('RAM boot deadline: ' + screen()[-1200:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='sr1010-zxdbg.itb')
    parser.add_argument('--mode', choices=('default', 'native', 'ipv4', 'ipv6'), required=True)
    args = parser.parse_args()
    if Path(args.image).name != args.image or not args.image.endswith('.itb'):
        parser.error('image must be a FIT basename under out/')
    blob = (ROOT / 'out' / args.image).read_bytes()
    if not screen().endswith('=>'):
        send('reboot -f')

        def interrupted(text):
            if 'Hit any key' in text or 'autoboot' in text:
                subprocess.run(['tmux', 'send-keys', '-t', PANE, 'C-c'], check=True)
            return text.endswith('=>')

        wait(interrupted, 30)
    send('tftpboot ' + args.image)
    transfer = wait(lambda text: text.endswith('=>') and
                    f'Bytes transferred = {len(blob)} ' in text, 60)
    parameters = {'default': '', 'native': 'zx279133.rx_hash=0', 'ipv4': 'zx279133.rx_hash=1 zx279133.rx_hash_ipv6=0',
                  'ipv6': 'zx279133.rx_hash=1 zx279133.rx_hash_ipv6=1'}[args.mode]
    send('setenv bootargs console=ttyAMA0,115200 earlycon rdinit=/init ' + parameters)
    wait(lambda text: text.endswith('=>'), 5)
    send('bootm')
    boot = wait(lambda text: 'sr1010-zxdbg#' in text and 'Link is Up - 2.5Gbps' in text, 60)
    print(json.dumps({'image': args.image, 'mode': args.mode, 'bytes': len(blob),
                      'sha256': hashlib.sha256(blob).hexdigest(), 'transfer': transfer, 'boot': boot}, indent=2))


if __name__ == '__main__':
    main()
