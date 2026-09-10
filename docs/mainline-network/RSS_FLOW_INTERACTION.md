# RSS diagnostic: hardware-flow coexistence and interface lifecycle

Successor: [per-interface RX worker split](RX_WORKER_SPLIT_ACCEPTANCE.md)
implements the two-worker driver path and identifies UMEM firmware reload as
a hash-installation lifecycle boundary. The following is the preceding RX1 stage.

2026-09-07: **the guarded PPU hash coexists with the tested bidirectional
hardware UDP NAT flow, and survives a complete WAN/LAN interface down/up
cycle.** The external module now reads back its live hooks and bodies when
status is requested. Forty-nine independent checks pass, with explicit limits
on malformed-packet observations.

This extends [format-guard acceptance](RSS_FORMAT_FALLBACK.md). The normal FIT
is unchanged; this work does not enable production RSS/RXHASH, SG or TSO.
Linux still has RX1/TX2 per interface and one RX worker per interface.

## Hardware-hit evidence

The existing `tc-udp-flow.sh` installs two exact IPv4 UDP NAT rules using
`skip_sw`, port 5301 and unused preference 38101. LAN source 192.168.5.100 is
translated to 192.168.1.1 toward WAN peer 192.168.1.100; the reverse rule restores
the LAN destination. Initial TC ingress filters and nftables rules were empty.

The new [endpoint fixture](../../port/mainline/rss-acceptance/flow-echo-probe.py)
checks every 256-byte request and echo at both endpoints, including sequence
and translated peer address. It injects LAN frames through the existing Npcap
device because Windows has no route for the WAN test subnet. It changes no
host address, route or adapter state. WAN replies use its normal local subnet.

| Phase | Requests and replies | Bad bytes/messages | Hardware hits per direction | CPU RX descriptors |
| --- | --- | --- | --- | --- |
| Original microcode | 128 / 128 | 0 | +128 / +128 | 0 on all 16 rings |
| Guarded hash installed | 128 / 128 | 0 | +128 / +128 | 0 on all 16 rings |
| Hash module unloaded | 128 / 128 | 0 | +128 / +128 | 0 on all 16 rings |

The [three-phase records](../../out/acceptance/rss/flow/) contain endpoint
results and full before/after TC and driver counters. TC's repeated action
statistics are deduplicated as one count per hardware direction, not summed.
Hardware-accounted byte deltas are 39168 and 38656 per run; endpoint payload
bytes are 32768 per direction. These distinct accounting quantities are kept
separate. RX/DMA/map/refill/TX-timeout error deltas remain zero.

Matched [WAN](../../out/acceptance/rss/flow/flow-hit-wan-ipv4-udp.json) and
[LAN](../../out/acceptance/rss/flow/flow-hit-lan-ipv4-udp.json) instruction traces
complete at PC937, bypassing PC1558/1893 and all inserted instructions. They
have 219/189 samples, restored=0 and no stopped context. These observations
establish the actual path for the tested manual TC UDP flow, not universal
TCP, IPv6, automatic nftables or arbitrary action-list coexistence.

## Live readback and interface down/up

The [module](../../port/mainline/rss-acceptance/rx-ppu-hash.c) now reports
`live_read` and `mismatched_blocks`. It reads both installed hook words and
every body block from instruction SRAM and compares them with the expected
program. The software `hooked` count alone is no longer the evidence used to
judge whether a lifecycle event retained the patch.

After removing the two TC rules and the two clsact qdiscs created for this test,
the sequence was `lan1 down`, `lan-cpu0 down`, `eth0 down`, then all three up.
[Recovered serial evidence and independent readback](../../out/acceptance/rss/flow/cycle.json)
show both links returning and `live_read=0 mismatched_blocks=0`, with no stopped
context and IRQ masks 1/2 retained. An informational kernel log split the serial
start marker, so the collector timed out even though the commands completed;
the record explicitly distinguishes this collection failure from the subsequent
successful live readback.

[Post-cycle UDP](../../out/acceptance/rss/flow/cycle-udp.json) passes all 8192
sequence-checked packets: zero missing, bad, duplicate or out-of-order packets,
and exactly 512 descriptors on each of 16 rings.
[Post-cycle TCP](../../out/acceptance/rss/flow/cycle-tcp.json) passes 64 MiB per
port, with zero bad bytes. This is an interface stop/start result; it is not
a claim that an external module is automatically restored after every PPU,
power or firmware-reset mechanism.

## Malformed-frame boundary

Eight exact malformed fixtures were compared with and without the patch:
bad IPv4 checksum, IHL 4, short/long IPv4 total length, a truncated frame,
short/long UDP length, and TCP data offset 4. Some corruptions also invalidate
transport checksums; these are explicit byte fixtures rather than isolated
proofs of each parser validator.

Seven fixtures yield no matched PC561 context in either phase: result=-61,
count 0, restored 0, and no remaining stopped context. Their raw records retain
the unsuccessful trace result. **This does not locate a hardware drop or prove
that no other microcode entry ran.** The audit only accepts the unchanged A/B
observation; these cases do not become successful rejection tests.

The truncated fixture does reach PC1558 in both phases. Its IPv4 total length
still declares the original packet, but the wire is cut after the UDP ports
and padded to 64 bytes. The guard hashes the still-present parsed four-tuple;
the remaining native PC sequence is unchanged. Thus the guard is a parsed
protocol/fragment filter, not a complete packet-length or checksum validator.
Neither successful application delivery nor final kernel rejection of this
fixture is claimed. This remains an explicit boundary before broad malformed
traffic acceptance.

## Validation and recovery

[Independent audit](../../out/acceptance/rss/flow/audit.json): 49 checks PASS
across the flow counters, endpoint content, bypass traces, bounded A/B
observations, live readback, post-cycle integrity and final restoration.
[Auditor source](../../port/mainline/rss-acceptance/audit-offload-interaction.py)
keeps unmatched cases and their limits visible.

The external module builds with W=1 and passes strict checkpatch without
errors, warnings or checks. New/updated Python scripts pass AST checks. The
build logged a 0.0015-second make timestamp-skew warning; the
[dependency recheck](../../out/acceptance/rss/flow/build-recheck.log) then
completed without warnings or pending work.

[Final unload](../../out/acceptance/rss/flow/final-unload.json) records matching
live instructions, `hooks/body restore=0`, no trace/hash module and restored
original qdisc state. The board was freshly RAM-booted after verifying the
10,997,748-byte TFTP transfer. The unchanged FIT SHA-256 is
`7d846707d82c215413db292df30648b0cf7909407705cf238879bfe5f1c36ca1`.

The first recovery TCP attempt started while LAN carrier was still 0 and its
sender failed to become ready; that attempt is retained separately. Once both
carriers were confirmed up, [recovery TCP](../../out/acceptance/rss/flow/recovery-incoming.json)
passed 64 MiB per port, queue 1/9 deltas zero, without affinity changes.
[Final state](../../out/acceptance/rss/flow/recovery-final.json) has taint 0,
only normal DSA modules, IRQ masks 1/2, XPS masks 0, empty kernel-error scan and
3/3 pings from each peer. No flash/NAND/saveenv or host topology changes occurred.

The next driver change is to separate interface identity from RX-worker
ownership: distribute each interface's hardware rings across the two workers,
keep packet delivery on the correct netdev, and record the corresponding Linux
RX queue. The shared refill/page-map and AF_XDP ownership rules must remain
correct. Production microcode installation/reset ownership, broader parser
boundaries, XDP/AF_XDP and performance remain gates before advertising RSS.
