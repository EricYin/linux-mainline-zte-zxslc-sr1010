# Continuous PPU tuple hash: RAM diagnostic acceptance

Successor: [format-guard and fallback acceptance](RSS_FORMAT_FALLBACK.md) adds
calibrated predicates and validates 25 packet-format traces. This page records
the earlier unguarded 40/48-word prototype; the linked module now includes guards.

2026-09-07: **self-contained PPU instructions now hash ordinary IPv4 TCP/UDP
flows and continuously select all eight hardware RX queues per port.** The
controlled WAN/LAN UDP matrix, sequence tests and TCP byte tests pass. This
advances beyond the earlier stopped-context replay; it does not yet implement
production RSS or spread one interface's RX work over multiple CPUs.

The production FIT remains unchanged: 10,997,748 bytes, SHA-256
`7d846707d82c215413db292df30648b0cf7909407705cf238879bfe5f1c36ca1`.
Linux still exposes RX1/TX2 per interface. RXHASH, ethtool RSS controls, SG and
TSO remain disabled. SG remains explicitly BLOCKED.

## Self-contained instruction path

The [external module](../../port/mainline/rss-acceptance/rx-ppu-hash.c) temporarily
branches from ordinary miss consumer PC1558 to PC6000 and SYN consumer PC1893
to PC6064. Bodies occupy 40 and 48 words respectively, including one NOP after
each generated operation. They return to the instruction after the displaced
consumer. Firmware and hook/body writes are read back before traffic tests.

The hash is the previously calibrated folded XOR:

```text
h = IPv4_source ^ IPv4_destination ^ (source_port << 16 | destination_port) ^ protocol
for shift in [16, 8, 4, 3]: h ^= h >> shift
queue = (native_queue & 8) | (h & 7)
```

The new late-hash replay first verified header-relative packet reads at the
consumer, eliminating the entry prototype's host-assisted scratch restoration:

| Instruction | Verified read |
| --- | --- |
| `0700800028180523` | IPv4 source, L3 + 12, into SPR2 |
| `0704000028200523` | IPv4 destination, L3 + 16, into SPR16 |
| `0704000008000523` | transport ports, L4 + 0, into SPR16 |
| `0704000028120520` | IPv4 protocol byte, L3 + 9, into SPR16 |

The ordinary miss path already establishes these bases in SPR6. The SYN body
executes the native base setup pair and restores SPR6 from SPR20[23:16] afterward.
SPR16 is cleared to its observed entry value before executing the original
queue read; the hash then replaces only its queue's low three bits. SPR2 retains
the computed hash. No packet-memory write or host-driven single-step is used
while the continuous module is installed.

[Late WAN UDP trace](../../out/acceptance/rss/late-hash-wan-udp.txt) and
[late LAN UDP / WAN+LAN SYN matrix](../../out/acceptance/rss/late-hash-matrix.json)
verify the relative reads and 17/20-operation bodies before continuous loading.
This is bounded register-liveness evidence for these paths, not proof for every
packet format or reason code that can reach the consumers.

## Instruction storage and restoration

An initial zero/filler check rejected PC6000, which contained
`038090064ac000a5`. A read-only scan found no zero/filler words in the unloaded
gap. The mainline loader writes only PCs 0..5271 and 12208..12287; it leaves the
gap's SRAM contents unspecified. The revised diagnostic checks all 5352 loaded
instructions against the firmware and checks that its body ranges are filler
in that firmware. The full loaded-image comparison passed before installation.

The module saves the gap's actual SRAM contents, writes/verifies both bodies,
then installs/verifies the two hooks. It restores hooks first on unload, allows
in-flight contexts to leave, checks for stopped/busy contexts, then restores and
verifies the original body words. Recognized direct branches in the loaded main
image do not target the gap. This is not a complete proof about indirect entry
or all runtime microcode users; the module remains a temporary isolated-board
diagnostic, never part of the normal FIT.

## Controlled packet acceptance

| Source UDP port | LAN RX queue | WAN RX queue | Per-port isolated count |
| --- | --- | --- | --- |
| 48000 | 3 | 11 | 128 |
| 48001 | 0 | 8 | 128 |
| 48002 | 5 | 13 | 128 |
| 48003 | 6 | 14 | 128 |
| 48004 | 7 | 15 | 128 |
| 48005 | 4 | 12 | 128 |
| 48006 | 1 | 9 | 128 |
| 48007 | 2 | 10 | 128 |

Source addresses are 192.168.1.100 / 192.168.5.100, destinations .1 on the same
subnets, destination port 49000. Each isolated run's target rings increased by
exactly 128 and every other ring by zero. The [matrix](../../out/acceptance/rss/continuous-udp-matrix.json)
records both-end counts and complete driver counter deltas.

The [simultaneous test](../../out/acceptance/rss/continuous-udp-concurrent.json)
interleaves eight flows per port, 512 packets per flow, 256 payload bytes per
packet, at a bounded sender pace. All 8192 packets arrived; each of the 16 rings
increased by exactly 512. Both receivers independently reported missing=0,
bad=0, order=0 and duplicate=0. Payload checks include the flow identity,
sequence, expected total and every pattern byte. This establishes order and
integrity for this bounded matrix, not line-rate performance or all flow sets.

The [TCP incoming test](../../out/acceptance/rss/continuous-tcp.json) transfers
eight 8-MiB streams per port simultaneously, 128 MiB total, with zero bad bytes.
TCP completion alone does not prove absence of packet reordering.

[Continuous same-tuple SYN/ACK injections](../../out/acceptance/rss/continuous-syn-ack.json)
exercise the two consumer paths separately. WAN SYN 2331 / ACK 2327 frames all
reach queue 15; LAN SYN 1924 / ACK 1913 all reach queue 7. Each target ring's delta
exactly matches the injector count. LAN runs also record small background
deltas on other queues; those are not counted as fixture hits. ACK cases alter
sequence, IPv4 ID and payload while preserving the four-tuple.

All claimed runs have zero RX error, invalid-DMA, page-map-miss, refill-shortfall
and TX-timeout deltas, and no outstanding refill deficit. The independent
[evidence auditor](../../port/mainline/rss-acceptance/audit-continuous-hash.py)
recomputes queues from endpoints/frame bytes and checks counts, order reports,
errors and restoration: [49 checks PASS](../../out/acceptance/rss/continuous-audit.json).
Both modules build with W=1 and pass strict checkpatch with zero errors,
warnings or checks; the UDP receiver builds with `-Wall -Wextra -Werror`.

## Recovery and remaining production gates

The [unload record](../../out/acceptance/rss/continuous-restore.json) has no
stopped context and `hooks/body restore=0`. A subsequent
[128-packet per-port control](../../out/acceptance/rss/continuous-after-unload.json)
again hits only native LAN 0 / WAN 8, with clean sequence/byte checks.

The board was then freshly RAM-booted from the unchanged FIT after verifying
the TFTP byte count. [Recovery TCP acceptance](../../out/acceptance/rss/continuous-recovery-incoming.json)
passes another 64 MiB per port without affinity changes; queue 1/9 deltas are zero.
[Final state](../../out/acceptance/rss/continuous-recovery-final.json) records
taint 0, only normal DSA modules, IRQ 22/25 masks 1/2, all XPS masks 0, no temporary
hash module, an empty kernel error scan and 3/3 pings from each peer. No
flash/NAND/saveenv writes or host address/topology changes occurred.

The next implementation gate is a format eligibility and fallback branch in
the continuous body: establish live predicates for ordinary unfragmented IPv4
TCP/UDP, preserve all other consumers unchanged, and test options, fragments,
IPv6, non-IP and offloaded-flow hits. Complete register/flag and instruction
storage ownership checks before integrating this into the driver. Then map
selected rings to multiple RX workers within each interface and expose accurate
Linux RX queue identities. XDP/AF_XDP, reset/link cycles, broader distribution
and performance acceptance remain required before advertising production RSS.
