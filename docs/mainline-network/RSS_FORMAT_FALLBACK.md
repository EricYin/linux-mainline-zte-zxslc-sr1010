# Continuous PPU hash: format guard and native fallback

Successor: [hardware-flow and lifecycle acceptance](RSS_FLOW_INTERACTION.md)
adds actual hardware-hit bypass traces, live SRAM verification and stop/start
tests. It also records malformed fixtures that are unmatched or still hashed;
the protocol guard below must not be read as a universal packet validator.

2026-09-07: **the continuous RAM diagnostic now checks parsed IPv4, fragment,
UDP and TCP predicates before hashing.** Twenty-five WAN/LAN format traces
verify the selected branch, hash or native queue result, register preservation
and unchanged packet bytes. Continuous UDP and TCP integrity tests also pass.
The independent trace auditor reports **375 checks PASS**.

This is a successor to [unguarded continuous hashing](RSS_CONTINUOUS_PPU_HASH.md).
Production RSS is still unfinished: the normal FIT has no hash patch, each
interface still exposes RX1/TX2, and one interface's rings still share one NAPI
worker. RXHASH/RSS controls, SG and TSO remain disabled; SG remains BLOCKED.

## Predicates established by execution

PC1893 also handles IPv4 ICMP and ARP; it is not exclusive to TCP SYN. PC1558
also receives IPv6 and non-IP frames. Merely reaching either hook is therefore
insufficient evidence that four-tuple loads are valid.

Stopped-context calibration varies individual SPR10 bits and executes the
actual branch instruction. Each direction of the two-target branch is tested:

```text
instruction = 0x0108000000000000 | false_target << 31 | true_target << 15 | condition
```

| Condition | Observed input |
| --- | --- |
| `0x2840` | SPR10 bit 2 |
| `0x2860` | SPR10 bit 3: IPv6 in the sampled parsed IP paths |
| `0x2880` | SPR10 bit 4: IPv4 |
| `0x28a0` | SPR10 bit 5: UDP |
| `0x28c0` | SPR10 bit 6: TCP |
| `0x2aa0` | SPR10 bit 21: fragment |
| `0x0520` | Always taken in all calibration inputs |

Seven conditions across eleven register values and both target orientations
give 154 verified cases. The [positive](../../out/acceptance/rss/formats/branch-positive.json)
and [swapped-target](../../out/acceptance/rss/formats/branch-swapped.json) records
also preserve 88 observations of four other candidate encodings. Those reject
a general high-bit inversion rule and are explicitly excluded from semantic
claims. The implemented guard uses only the verified positive predicates,
exchanging true/false destinations where necessary.

In real frames, IPv4 UDP/TCP have SPR10 values `0x35`/`0x55`; IPv6 UDP has
`0x2d`; fragments add bit21. IPv4 ICMP is `0x15`. Non-IP processing can itself
set bit3 later, so an isolated IPv6-like bit must not be treated as complete
protocol classification. The hash guard requires the IPv4 and transport bits
together and rejects the fragment bit.

## Implemented control flow

The [external module](../../port/mainline/rss-acceptance/rx-ppu-hash.c) uses four
conditional branches before any new packet read or scalar write:

1. IPv4 false: native fallback.
2. Fragment true: native fallback.
3. UDP true: hash body.
4. TCP true: hash body; otherwise native fallback.

Ordinary hook PC1558 now branches to a 48-word body at PC6000; hook PC1893 uses
52 words at PC6064. Fallbacks at PC6043/6113 execute the displaced native queue
read, a NOP and the return branch. The fallback changes only SPR16 as required
by that original instruction. Hash bodies retain the previously validated
L3/L4-relative loads, folded XOR and direction-preserving queue selection.

Native IPv4/IPv6 fragments already branch around both hooks. Their six tested
WAN/LAN paths remain outside the new code, with exactly the same PC sequence
as their native references. The new fragment predicate is also independently
calibrated, although ordinary fragment traffic does not reach it in this set.

## Packet and continuous acceptance

| Mode / format | Observed action | Queue result |
| --- | --- | --- |
| WAN + LAN IPv4 UDP | Hash | WAN11 / LAN3 |
| WAN + LAN IPv4 TCP SYN and ACK | Hash | WAN15 / LAN7 |
| WAN + LAN IPv4 UDP with options | Hash using updated L4 offset | WAN11 / LAN3 |
| WAN VLAN, QinQ and PPPoE IPv4 UDP | Hash using parsed header bases | WAN11 |
| WAN + LAN IPv6 UDP, plus WAN IPv6 extension header | Native fallback | WAN8 / LAN0 |
| WAN + LAN IPv4 ICMP | Native fallback | WAN13 / LAN4 |
| WAN + LAN non-IP Ethernet | Native fallback | WAN8 / LAN0 |
| WAN ARP | Native fallback | WAN12 |
| WAN IPv4/IPv6 first and later fragments; LAN IPv4 fragments | Original path outside hooks | Original path retained |

These 25 traces comprise eleven hash paths, eight fallback paths and six paths
outside the hooks. Source ports are 48000, destination port 49000 where present.
The auditor independently computes hashes from exact wire bytes, including
header offsets through tags/PPPoE and IPv4 options. In every fallback, the other
31 scalar registers and all sampled packet words are preserved across the
inserted path. In hash paths, only SPR2/SPR16 may change; the SYN-path SPR6
restoration also passes. All eighteen available native-reference PC sequences
match after excluding the inserted instructions.

Hardware target-ring deltas meet or exceed the injector count in every
hook-reaching case. Some runs have background traffic; those aggregate deltas
are not claimed as exact per-packet identities. Matched-context traces establish
the selected packet's actual branch and queue value. The encapsulation cases
prove these specific parsed packet paths, not complete VLAN/PPPoE interface,
tunnel, flow-offload or arbitrary malformed-header support.

[Concurrent UDP](../../out/acceptance/rss/formats/guarded-udp-concurrent.json)
again receives all 8192 packets with zero missing, bad, out-of-order or duplicate
packets; each of the 16 hardware rings increases by exactly 512. This remains a
bounded paced test, not a line-rate claim.
[Concurrent TCP](../../out/acceptance/rss/formats/guarded-tcp.json) passes eight
8-MiB streams per port, 128 MiB total, with zero bad bytes. Driver RX/DMA/map/
refill and TX-timeout errors stay zero in the claimed runs.

## Evidence, recovery and next gate

- [375-check independent audit](../../out/acceptance/rss/formats/format-audit.json)
  and [auditor source](../../port/mainline/rss-acceptance/audit-format-fallback.py).
- [Raw trace directory](../../out/acceptance/rss/formats/): explicit wire and PPU
  bytes, sparse register traces and bracketed driver counters where available.
- [Unloading](../../out/acceptance/rss/formats/guarded-restore.json) reports no
  stopped context and `hooks/body restore=0`. The
  [subsequent UDP control](../../out/acceptance/rss/formats/after-unload.json)
  passes 128 packets per port and hits only native queues 0/8; WAN8 also includes
  background traffic.

One native ARP collection timed out because a kernel log split the serial
command's ending nonce. Its successful, restored trace was recovered separately
and labelled `TRACE_ONLY`; its missing injector/post-counter data are not used
as counter acceptance. A short delay after insmod now lets the log complete
before printing the ending nonce.

Both external modules build with W=1 and strict checkpatch reports zero errors,
warnings and checks. The final build logged a 0.01-second make timestamp-skew
warning; an immediate dependency recheck completed without warnings or pending
work, recorded in [build-recheck.log](../../out/acceptance/rss/formats/build-recheck.log).
The new Python scripts pass AST checks. The production
FIT remains 10,997,748 bytes with SHA-256
`7d846707d82c215413db292df30648b0cf7909407705cf238879bfe5f1c36ca1`.
After verified TFTP transfer and fresh RAM boot,
[recovery TCP](../../out/acceptance/rss/formats/recovery-incoming.json) again
passes 64 MiB per port with queue 1/9 deltas zero and unchanged affinity.
[Final state](../../out/acceptance/rss/formats/recovery-final.json) has taint 0,
only normal DSA modules, IRQ masks 1/2, XPS masks 0, empty kernel-error scan and
3/3 pings from each peer. No flash/NAND/saveenv or host topology changes occurred.

Before integrating the patch into the driver, validate its interaction with
offloaded-flow hits and malformed/unsupported parser states, and settle
instruction-region ownership and reset lifecycle. Then split each interface's
hardware rings across RX workers/CPUs and expose accurate Linux RX queues.
XDP/AF_XDP, link/reset cycles and performance acceptance remain production gates.
