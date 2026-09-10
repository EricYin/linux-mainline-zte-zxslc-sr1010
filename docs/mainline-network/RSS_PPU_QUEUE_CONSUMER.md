# Ordinary RX PPU queue consumer: traced and perturbed

Follow-up: [RSS_PPU_HASH_PROTOTYPE.md](RSS_PPU_HASH_PROTOTYPE.md) supplies a
calibrated XOR primitive and packet-derived queue calculation in a single-context
diagnostic. The fixed-queue consumer and perturbation evidence below remain the
foundation; the final hash-operation search gate is superseded by that prototype.

2026-09-07: **the ordinary RX queue consumer is located and causally linked to
the hardware RX ring**. A single matched WAN context moved from queue 8 to 9;
a matched LAN context moved from 0 to 1. This is diagnostic steering, not RSS.
No production microcode or protocol table change remains.

Evidence: [combined JSON](../../out/acceptance/rss/ppu-queue-consumer.json),
six successful sparse traces beside it, and the
[reproducible auditor](../../port/mainline/rss-acceptance/audit-rx-trace.py).
Firmware SHA-256 is
`056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40`.
Instruction PC indexes big-endian 64-bit words at `16 + PC * 8` in this image.

## Actual entry and branches

The firmware's port-flow records name PC10 for ports 5/6. Mainline subsequently
programs the ordinary Ethernet entry as SPA attribute `0x463`, whose PC field
is 561, on ports 0/5/6. A PC10 diagnostic did not stop any context. At PC561,
the test frame was matched byte-for-byte in local packet words 32 onward and
its stopped context could be single-stepped to the final PC2217.

LAN ingress adds an 802.1Q VLAN 62 tag before this PPU entry. The first LAN
attempt with an untagged comparison correctly rejected all contexts; snapshots
showed the inserted `8100003e` bytes. Subsequent LAN comparisons included the
entire actual tagged frame. The wire fixture itself remained untagged.

Observed paths, omitting straight-line subranges:

- UDP: PC561 → 596 → 607 → 610 → 622 → 634 → 661 → 1545…1563 → 2189…2217.
- TCP SYN: PC561 → 596 → 607 → 608 → 609 → 1873…1898 → 2189…2217.

These are measured successors for these packets, not decoded general branch
predicates. WAN/LAN traces contain 119/121 UDP samples and 94/96 SYN samples.
The SYN path loads protocol reason 83; the UDP lookup-miss path uses reason 84.
Immediately before the response operation, SPR3 is `0x353` for WAN SYN and
`0x354` for WAN UDP, consistent with `(port 6 << 7) + reason`. The instruction
at PC1876/1548 is `0018061800400533`. The SYN trace observes RSP0 changing from
the previous `60300000` value to `70300000` at the next stop. In the UDP case
the response is the same as its stale initial value; equality alone is not
evidence that no table read occurred.

## Queue read and subsequent transform

PC1558 (UDP) and PC1893 (SYN) execute the same instruction:
`0504210680000523`. The four natural traces establish this sampled relation:

`SPR16 = ((RSP0 >> 26) & 0xf) << 16`

RSP0's upper halfword is the recovered protocol action; queue bits are its
13:10. The following NOP provides the next stopped sample containing the result.

| Packet | RSP0 at consumer | Native queue | SPR16 after read | SPR16 after transform |
| --- | --- | --- | --- | --- |
| WAN UDP | `60300000` | 8 | `00080000` | `019b0000` |
| WAN TCP SYN | `70300000` | 12 | `000c0000` | `019f0000` |
| LAN UDP | `40140000` | 0 | `00000000` | `01930000` |
| LAN TCP SYN | `58180000` | 6 | `00060000` | `01990000` |

PC1560/1895 executes `020420840064c1ef`; its observed result is
`SPR16 = (0x193 + queue) << 16`. The queue-1/9 perturbations below give two
additional points that distinguish this addition from a simple OR operation.
SPR16 remains unchanged from that transform through the final sampled PC2217.
All packet words 32..63 also remain unchanged across all six successful traces.

The final common path includes PC2216 `01100000088d3180` and PC2217
`0018000000000000`. The precise hardware operation and the meaning of base
`0x193` are not decoded here. Final context disappearance alone is not called
ring delivery; the independent descriptor counts below provide that link.

## Single-context queue perturbation

The external diagnostic module restores the temporary entry breakpoint before
single-stepping one matched context, and resumes the other stopped contexts.
An explicit optional `queue` parameter replaces only the consumer instruction
in the selected context's replay latch. It uses the previously calibrated
upper-immediate form to set SPR16's high half to the requested queue. The
original instruction must match exactly, SPR16's low half must be zero, and the
requested queue must stay within the same eight-queue WAN/LAN group. No protocol
table entry changes and no WOE/SG submission occurs.

| Probe | Replayed PC1558 instruction | SPR16 read / transformed | Ring counter result |
| --- | --- | --- | --- |
| WAN 8 → 9 | `010420001200052f` | `00090000` / `019c0000` | q9 +1; q8 +1985, including background traffic |
| LAN 0 → 1 | `010420000200052f` | `00010000` / `01940000` | q1 +1; q0 +1916; injector sent 1917 frames |

Both traces report exactly one replacement, restored instruction readback
success, no remaining stopped contexts, and unchanged sampled packet bytes.
RX errors, invalid DMA, ownership lookup misses, refill shortfalls and TX timeout
deltas are zero. This links the queue-consumer result to the actual RX ring.
There was no separate userspace byte capture of the individual replayed frame;
the sampled local bytes and descriptor delivery evidence must not be presented
as full end-to-end acceptance of a production steering feature.

## Recovery and checks

The module and temporary HTTP trace-export server were stopped after each run.
After the probes, the board was freshly RAM-booted from the unchanged accepted
FIT: 10,997,748 bytes, SHA-256
`7d846707d82c215413db292df30648b0cf7909407705cf238879bfe5f1c36ca1`.
TFTP exceeded the first helper wait with retransmissions; its final exact byte
count was verified before bootm. No flash/NAND or saved environment writes.

On that clean boot, simultaneous WAN/LAN incoming traffic passed eight streams
and 64 MiB per port, zero bad bytes, without changing the default IRQ affinity.
Queue 1 and queue 9 deltas both remained zero, confirming the temporary steering
did not persist. NAPI overlap counters increased by 368/230. Both peers then
passed 3/3 pings. Final kernel taint is zero; only the normal DSA modules are
loaded, affinities remain LAN CPU0/WAN CPU1, and the kernel error scan is empty.
Host topology and addressing are unchanged. SG remains disabled and BLOCKED.

The external module builds with W=1; strict checkpatch reports zero errors,
warnings and checks. Python syntax and the six-trace auditor pass. The auditor
checks firmware identity, every executed instruction against firmware or the
explicit calibrated replacement, complete initial state, sample sequence,
queue-read result, later SPR16 stability, and restored/stopped state. It reports
packet-memory invariance separately. This is a bounded trace decoder, not a
complete PPU ISA implementation. See [build log](../../out/acceptance/rss/ppu-trace-build.log).

## Remaining RSS work

The unknown is now the stable tuple-derived hash computation or hash source,
not where ordinary receive chooses its queue. In these sampled paths the
consumer uses the fixed protocol action, and no later operation changes SPR16
after the constant-base transform. Initial SPR/RSP values can be leftovers from
previous packets; they must not be advertised as hardware RX hashes.

Next work can target a replacement immediately before this verified queue
transform, while preserving the WAN/LAN group bit. It still needs a verified
hash operation or source, register-liveness analysis, instruction-space/control
flow constraints, same-flow order and distribution tests, and fragment/tunnel
fallback behavior. Only then can production RX queues or RSS controls be exposed.
