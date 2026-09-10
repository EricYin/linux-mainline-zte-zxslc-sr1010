# PPU tuple-hash prototype and instruction calibration

Successor: [continuous PPU hash acceptance](RSS_CONTINUOUS_PPU_HASH.md) replaces
host-driven replay with temporary self-contained bodies and validates ordered
UDP plus TCP byte integrity. The following records the preceding replay stage.

2026-09-07: **the PPU now computes a packet-derived XOR hash in a bounded
single-context diagnostic, and the selected packet reaches the calculated RX
ring**. Twenty-four probes pass independent calculation and trace checks.
This is not continuous production RSS: debug single-step/replay drives the
extra instructions, and other packets retain their normal fixed queues.

Production FIT remains unchanged, with WAN/LAN independent NAPI and default
CPU placement. Linux still exposes RX1/TX2 per interface; RX hash, RSS key and
indirection controls are not advertised. SG remains BLOCKED and disabled.

## Recovered operations

The preceding [queue-consumer work](RSS_PPU_QUEUE_CONSUMER.md) located the
destination at PC1558/1893. This stage calibrates operations needed to calculate
its input rather than substituting a host-selected constant queue.

For the observed unconditional bit-copy form:

| Instruction bits | Field |
| --- | --- |
| 50:46 | destination SPR |
| 45:41 | destination bit offset |
| 40:35 | source register; 0..31 SPR, 32..63 response window |
| 34:30 | source bit offset |
| 4:0 | width minus one |
| 15 | clear other destination bits before insertion |

Base is `0500000000000520`. The model matches 104 sampled operations comprising
28 unique instructions across the four natural WAN/LAN UDP/SYN traces. This
does not decode other condition fields or claim a complete ISA.

The diagnostic then saved scalar registers, loaded known operand patterns,
executed a small set of register-operation forms, and restored changed scalar
registers before the native consumer continued. Its calibrated data operations
include 16-bit addition/subtraction, AND, AND-NOT, XOR, XNOR, and logical shifts
whose count uses the second operand's low five bits. Unsigned min/max results
also match the selected patterns but are not used by the hash prototype.
Four carry-dependent cases are retained and explicitly excluded from the
auditor's arithmetic claims.

The useful XOR form is:

`06200000000003ff | (dst << 46) | (src_a << 35) | (src_b << 24)`

For example, `0620c018080003ff` computes SPR3 = SPR3 XOR SPR8:

| A | B | Observed result |
| --- | --- | --- |
| `0000a55a` | `00003c69` | `00009933` |
| `00005aa5` | `0000c396` | `00009933` |
| `ffffffff` | `55aa00ff` | `aa55ff00` |
| `00000000` | `00000000` | `00000000` |

Setting bit 10 in this form inverts the second operand, producing XNOR.
The neighboring calibrated AND form behaves similarly with operand inversion.
The candidates initially suspected to be XOR at selectors 9/10 proved to be
left/right shifts; they were not promoted based on opcode appearance.

[ALU capture](../../out/acceptance/rss/ppu-alu-xor.txt) contains all 44 cases;
40 are independently checked. ALU calibration changes flag state
(`01840000` to `01410000` in this run). Scalar restoration is verified, but the
calibration must not be described as flag-neutral. The native traced context
still completes with instruction restoration and no remaining stopped ME.

## Hash computed by the PPU

The selected context starts at PC561, where packet metadata words 14/15 still
contain DIP/SIP. Nineteen PPU load, bit-copy and XOR instructions compute:

```text
h = SIP ^ DIP ^ ((source_port << 16) | destination_port) ^ IP_protocol
raw = h
h ^= h >> 16
h ^= h >> 8
h ^= h >> 4
h ^= h >> 3
queue = (native_protocol_queue & 8) | (h & 7)
```

This is a fixed experimental XOR folding scheme, not Toeplitz. Addresses and
ports use the numeric values reconstructed from network-order fields. The PPU
loads the packet data; the host calculates an independent expected value only
for verification. The host does not supply the selected queue in hash mode.

SPR2 retains the hash until the queue consumer. SPR3/SPR8 are scratch operands
and are restored to their entry values. The original consumer first supplies
the native direction bit. `0504201000000522` then copies only hash bits 2:0 into
SPR16 bits 18:16, preserving the WAN/LAN group bit. The existing constant-base
transform and output path subsequently run unchanged.

The prototype requires basic, nonfragmented IPv4 with a 20-byte header and TCP
or UDP. It accepts the observed single VLAN transport form for LAN and adjusts
the packet-word reads accordingly. This restriction is enforced before arming
the diagnostic; it is not a production classifier or fallback implementation.

## Actual queue results

For UDP to destination port 49000, varying source ports gives:

| Source port | WAN queue | LAN queue |
| --- | --- | --- |
| 48000 | 11 | 3 |
| 48001 | 8 | 0 |
| 48002 | 13 | 5 |
| 48003 | 14 | 6 |
| 48004 | 15 | 7 |
| 48005 | 12 | 4 |
| 48006 | 9 | 1 |
| 48007 | 10 | 2 |

All 16 calculations match the stopped-context hash and the consumer result.
Every nondefault queue in this matrix increases by exactly one descriptor.
Queues 8/0 also receive the unselected native fixture packets; their aggregate
counts are not single-packet attribution. Counter snapshots exclude HTTP trace
export, whose own TCP SYN would otherwise contaminate a control-queue count.

Eight further cases verify:

- Repeating WAN UDP source port 48000 returns queue 11 again.
- Changing IPv4 ID and the final payload byte preserves WAN queue 11 and LAN
  queue 3 for that same UDP tuple, with recomputed valid packet checksums.
- Changing a higher source-port byte (48256) changes WAN queue to 10.
- TCP source port 48000 uses WAN queue 15 / LAN queue 7 for both SYN and ACK.
  The ACK also changes sequence number, IPv4 ID and payload. SYN reaches
  consumer PC1893 while ACK reaches PC1558, yet the queue stays the same.

Across all 24 probes, 22 cases have exactly one descriptor added to the expected
nondefault queue; the other two select their native queue. RX/DMA/map/refill
and TX-timeout error deltas remain zero. The auditor verifies all selected frame
bytes against the entry snapshot, hash persistence in SPR2, scratch-register
restoration, consumer output, final SPR16 value and unchanged packet words.
This does not provide a userspace per-packet queue/hash association or prove
packet order under continuous traffic.

## Evidence and recovery

- [Independent audit summary](../../out/acceptance/rss/hash-audit.json): 104
  bit-copy checks, 40 ALU checks, four explicitly unclaimed carry cases, and
  24 frame-derived hash/consumer checks, all claimed checks PASS.
- [Per-flow evidence directory](../../out/acceptance/rss/hash/): each case
  contains exact wire/PPU frame bytes, sparse trace, before/after counters and
  independently predicted hash/queue. Repeat and SYN/ACK cases are separate
  JSON arrays.
- [Diagnostic source and usage](../../port/mainline/rss-acceptance/README.md):
  module, bounded injector, matrix runner and independent auditors.

The external module builds with W=1; checkpatch reports zero errors, warnings
and checks. Python AST checks and the independent audit pass. The
[build log](../../out/acceptance/rss/hash-probe-build.log) is preserved.

All diagnostic modules and temporary HTTP exporters were stopped. The board
was freshly RAM-booted from the unchanged accepted 10,997,748-byte FIT, SHA-256
`7d846707d82c215413db292df30648b0cf7909407705cf238879bfe5f1c36ca1`.
TFTP byte count was verified before bootm. No flash/NAND/saveenv or host address
changes occurred.

[Recovery incoming test](../../out/acceptance/rss/hash-recovery-incoming.json)
passes simultaneous eight-stream WAN/LAN traffic, 64 MiB per port, zero bad
bytes, without changing affinity. Queue 1/9 deltas are zero; no temporary hash
steering persists. NAPI overlaps are 378/249. Both peers subsequently pass 3/3
pings. [Final state](../../out/acceptance/rss/hash-recovery-final.json) records
taint zero, only normal DSA modules, affinities 1/2, no pending TX or RX deficit,
and an empty kernel error scan.

## Next production gate

The missing pieces are now integration and acceptance, not an unidentified
queue consumer or an unverified XOR primitive:

1. Choose and validate a continuous insertion point and instruction region,
   with register/flag liveness and restore rules. The current diagnostic uses
   host-driven replay/return and host-assisted scratch restoration; these must
   be replaced by a self-contained PPU instruction path. Fast-path flow-table
   hits and unsupported packet formats must retain their existing behavior.
2. Distribute each interface's selected queues across RX workers/CPUs and expose
   accurate Linux RX queue identifiers. Current WAN/LAN NAPI grouping still
   places all WAN rings on one worker and all LAN rings on the other.
3. Validate simultaneous independent flows, same-flow ordering, ring reuse,
   fallback formats, XDP/AF_XDP, reset and link cycles. Measure performance and
   hash distribution beyond this selected eight-port matrix before making
   throughput or production RSS claims.

No RSS feature bits or ethtool RSS controls have been enabled by this work.
