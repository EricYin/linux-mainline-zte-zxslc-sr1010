#!/usr/bin/env python3
"""Emit IPv4/IPv6 PPU RSS programs from calibrated instruction forms."""
import argparse
import json


def copy(dst, offset, src, bit, width, clear=False):
    return (0x0500000000000520 | dst << 46 | offset << 41 | src << 35 |
            bit << 30 | (width - 1) | (0x8000 if clear else 0))


def xor():
    return 0x06208010100003ff


class Program:
    def __init__(self, hook, start):
        self.hook, self.start = hook, start
        self.code, self.maps, self.labels, self.fixups = [], [], {}, []

    def emit(self, instruction):
        self.code.extend((instruction, 0))

    def label(self, name):
        self.labels[name] = self.start + len(self.code)

    def branch(self, condition, yes, no=None):
        self.fixups.append((len(self.code), condition, yes, no))
        self.code.append(0)

    def fold(self):
        for shift in (16, 8, 4, 3):
            self.emit(copy(16, 0, 2, shift, 32 - shift, True))
            self.emit(xor())

    def ipv4(self):
        for instruction in (0x0700800028180523, 0x0704000028200523, xor(),
                            0x0704000008000523, xor(), 0x0704000028120520,
                            copy(16, 0, 16, 0, 8, True), xor()):
            self.emit(instruction)
        self.fold()

    def ipv6(self):
        for index in range(8):
            self.emit(0x0700000028000523 | (16 if index else 2) << 46 | (8 + index * 4) << 17)
            if index:
                self.emit(xor())
        self.emit(0x0704000008000523)
        self.emit(xor())
        # Use the parsed transport, including when the base header precedes extensions.
        self.branch(0x28a0, 'udp6', 'tcp6')
        self.label('udp6')
        self.emit(0x010400000000852f | 17 << 25)
        self.branch(0x0520, 'protocol6')
        self.label('tcp6')
        self.emit(0x010400000000852f | 6 << 25)
        self.label('protocol6')
        self.emit(copy(16, 0, 16, 0, 8, True))
        self.emit(xor())
        self.fold()

    def map_tree(self, port, depth=0, bucket=0):
        start = self.start + len(self.code)
        if depth == 3:
            self.emit(copy(10, 0, 16, 0, 32))
            self.emit(0x010400000000852f)
            self.emit(0x0504210680000523)
            self.maps.append((len(self.code), bucket, port))
            self.emit(0x0104200000000522 | bucket << 25)
            self.branch(0x0520, self.hook + 1)
        else:
            at = len(self.code)
            self.code.append(0)
            no = self.map_tree(port, depth + 1, bucket)
            yes = self.map_tree(port, depth + 1, bucket | (1 << depth))
            self.fixups.append((at, 0x2880 + depth * 32, yes, no))
        return start

    def finish(self):
        for at, condition, yes, no in self.fixups:
            yes = self.labels[yes] if isinstance(yes, str) else yes
            no = self.labels[no] if isinstance(no, str) else no
            if no is None:
                no = self.start + at + 1
            self.code[at] = 0x0108000000000000 | no << 31 | yes << 15 | condition
        while len(self.code) % 4:
            self.code.append(0)
        return self


def build(which):
    p = Program((1558, 1893, 2216)[which], (6000, 6288, 6592)[which])
    p.branch(0x2880, 'fragment', 'version6')
    p.label('version6')
    p.branch(0x2860, 'fragment', 'fallback')
    p.label('fragment')
    p.branch(0x2aa0, 'fallback', 'udp')
    p.label('udp')
    p.branch(0x28a0, 'body', 'tcp')
    p.label('tcp')
    p.branch(0x28c0, 'body', 'fallback')
    p.label('body')
    if which == 2:
        p.emit(0x040000f80000069b)
        p.emit(copy(31, 0, 16, 0, 32))
        # Some CPU trap paths retain IP/L4 flags but discard both header offsets.
        # Preserve SPR10 while testing each byte of SPR18's L3/L4 offsets.
        p.emit(copy(2, 0, 10, 0, 32))
        p.emit(copy(10, 0, 18, 0, 16, True))
        for bit in range(8, 16):
            p.branch(0x2800 + bit * 32, 'l3_offset', 'no_offsets' if bit == 15 else None)
        p.label('l3_offset')
        for bit in range(8):
            p.branch(0x2800 + bit * 32, 'valid_offsets', 'no_offsets' if bit == 7 else None)
        p.label('valid_offsets')
        p.emit(copy(10, 0, 2, 0, 32))
        p.branch(0x0520, 'have_offsets')
        p.label('no_offsets')
        p.emit(copy(10, 0, 2, 0, 32))
        p.emit(copy(16, 0, 31, 0, 32))
        p.emit(0x0707c00018680523)
        p.branch(0x0520, 'fallback')
        p.label('have_offsets')
    if which:
        p.emit(0x0601a092148000e7)
        p.emit(0x06018090148000e7)
    p.branch(0x2860, 'hash6', 'hash4')
    p.label('hash4')
    p.ipv4()
    p.branch(0x0520, 'computed')
    p.label('hash6')
    p.ipv6()
    p.label('computed')
    if which == 2:
        for inst in (copy(16, 0, 31, 0, 32), 0x0707c00018680523,
                     0x040000100000069b, copy(2, 0, 31, 0, 32),
                     0x0707c00018600523, 0x0107fe0002000520,
                     0x040000f80000061b, copy(31, 0, 2, 0, 32),
                     0x0700800018680523):
            p.emit(inst)
        p.code.extend((0x01100000088d3180, 0x0018000000000000))
        p.label('fallback')
        for inst in (copy(2, 0, 31, 0, 32), 0x0707c00018600523,
                     0x0107fe0000000520, 0x040000f80000061b, copy(31, 0, 2, 0, 32)):
            p.emit(inst)
        p.code.extend((0x01100000088d3180, 0x0018000000000000))
    else:
        if which:
            p.emit(copy(6, 0, 20, 16, 8, True))
        p.emit(copy(16, 0, 10, 0, 32))
        p.emit(copy(10, 4, 2, 0, 3))
        p.emit(copy(10, 3, 32, 29, 1))
        at = len(p.code)
        p.code.append(0)
        lan = p.map_tree(1)
        wan = p.map_tree(0)
        p.fixups.append((at, 0x2860, wan, lan))
        p.label('fallback')
        p.emit(0x0504210680000523)
        p.branch(0x0520, p.hook + 1)
    return p.finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    programs = [build(which) for which in range(3)]
    for p, end in zip(programs, (6288, 6592, 7000)):
        assert p.start + len(p.code) <= end and len(p.code) <= 288
    if args.json:
        print(json.dumps([{'hook': p.hook, 'start': p.start, 'code': p.code,
                           'maps': p.maps, 'labels': p.labels} for p in programs]))
        return
    print('/* SPDX-License-Identifier: GPL-2.0-only */')
    print('/* Generated by port/mainline/rss-acceptance/generate-ipv6-rss.py. */')
    print('static const struct zx279133_rx_hash_program zx279133_rx_hash_ipv6_programs[] = {')
    for p in programs:
        print('\t{')
        print(f'\t\t.hook = {p.hook}, .start = {p.start}, .count = {len(p.code)},')
        if p.maps:
            print(f'\t\t.map_count = {len(p.maps)},\n\t\t.map = {{')
            for offset, bucket, port in p.maps:
                print(f'\t\t\t{{ {offset}, {bucket}, {port} }},')
            print('\t\t},')
        print('\t\t.inst = {')
        for offset, inst in enumerate(p.code):
            print(f'\t\t\t0x{inst:016x}ULL, /* {p.start + offset} */')
        print('\t\t},\n\t},')
    print('};')


if __name__ == '__main__':
    main()
