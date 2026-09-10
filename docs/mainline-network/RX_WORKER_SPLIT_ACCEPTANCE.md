# Per-interface RX worker split

Successor: [integrated hash lifecycle acceptance](RSS_HASH_LIFECYCLE_ACCEPTANCE.md)
now installs hashing in the driver, including UMEM reloads. External-module
limitations below describe this earlier worker-only acceptance image.

2026-09-08: **the driver now assigns half of each interface's hardware RX
queues to each of two NAPI workers.** WAN and LAN conduit expose RX2/TX2;
received packets carry the corresponding Linux RX queue index. With the
already validated external PPU hash, both interfaces independently distribute
ordered flows across both CPUs. Forty independent acceptance checks pass.

The running accepted RAM FIT is now 10,997,768 bytes, SHA-256
`4622490d6996e3162d9f8094ace06ea690bc8383741cea45513b4d49db088de5`.
The previous FIT remains at `out/sr1010-pre-rx-workers.itb`. The current image
contains the worker/queue driver changes but no continuous hash patch. RXHASH,
RSS key/indirection controls, SG and TSO remain disabled; SG remains BLOCKED.

## Driver changes

| Hardware queues | Interface | Linux RX queue | Worker | Default IRQ / CPU |
| --- | --- | --- | --- | --- |
| 0..3 | LAN conduit | 0 | 1 | IRQ22 / CPU0 |
| 4..7 | LAN conduit | 1 | 0 | IRQ25 / CPU1 |
| 8..11 | WAN | 0 | 0 | IRQ25 / CPU1 |
| 12..15 | WAN | 1 | 1 | IRQ22 / CPU0 |

The existing interrupt assignment masks are now `0x0ff0` for worker0 and
`0xf00f` for worker1. Interrupt masking and poll ownership use these same
disjoint sets. The original default LAN queue0 and WAN queue8 retain their
default CPU placement. Interface selection still uses hardware queue0..7
versus8..15, independently of the worker processing the descriptor.

The [IDM receive path](../../linux-6.18.38/drivers/net/ethernet/zte/zx279133-idm.c)
records Linux queue `(hardware_queue & 7) >> 2`, with separate XDP RX queue
objects and NAPI identities for WAN queues0/1. Both netdev allocations and
ethtool channel limits are updated. New per-worker `lan_descs`/`wan_descs`
statistics sum the existing per-ring counters under their statistics sequence
lock; they do not add a shared write in the receive path.

Two page pools, shared buffer-map/refill locking, foreign-page recycling and
TX reclaim serialization are retained. The old `lan_napi` field names identify
the second worker internally and no longer imply exclusive LAN ownership.

DSA user ports are still allocated with one RX queue by the DSA core. The
board's tag driver therefore records queue0 after resolving the user netdev;
it does not propagate conduit queue1 as an invalid user-port queue. This keeps
the existing DSA interface contract while the conduit handles LAN packets on
both CPUs. No general DSA-core queue allocation change is made.

## Ordered flows and byte integrity

Without the hash module, [baseline TCP](../../out/acceptance/rss/workers/baseline-tcp.json)
passes eight 8-MiB streams per port simultaneously. This verifies the new driver
with the original protocol queue selection before testing distribution.

With the guarded hash module installed, the 512-packet-per-flow UDP fixtures
give the following per-worker descriptor deltas:

| Traffic | Worker0 LAN / WAN | Worker1 LAN / WAN | Receiver result |
| --- | --- | --- | --- |
| WAN only | 0 / 2048 | 0 / 2048 | 4096 packets PASS |
| LAN only | 2050 / 0 | 2048 / 0 | 4096 packets PASS |
| Both | 2050 / 2048 | 2049 / 2048 | 8192 packets PASS |

The extra LAN descriptors are background traffic. All fixture packets have
zero missing, bad, out-of-order or duplicate observations. In these ordinary
page-pool tests, worker0 polls only on CPU1 and worker1 only on CPU0. The
[WAN](../../out/acceptance/rss/workers/hashed-udp-wan.json),
[LAN](../../out/acceptance/rss/workers/hashed-udp-lan.json) and
[simultaneous](../../out/acceptance/rss/workers/hashed-udp-both.json) records
preserve both receiver reports and full counter deltas.

[Hashed TCP](../../out/acceptance/rss/workers/hashed-tcp.json) passes 64 MiB per
port with zero bad bytes. Both workers process approximately half of each
interface's descriptors. These tests establish correctness and actual CPU
distribution; no throughput improvement or line-rate claim is made.

## XDP and AF_XDP

The XDP_PASS helper now has a `queues` mode using atomic BPF array counters for
`rx_queue_index`, including an out-of-range bucket. During another simultaneous
8192-packet UDP test, [XDP reports](../../out/acceptance/rss/workers/xdp-queues.json)
WAN queue0=2048, queue1=2048, unexpected=0. Both receivers pass content and order
checks, and detachment succeeds. This tests the queue identities delivered to
XDP, rather than inferring them from sysfs alone.

AF_XDP keeps its existing shared-UMEM, queue0-only contract. While attached,
both IRQ groups schedule worker0 and that worker polls all 16 rings. The
[ordinary UDP test during zero-copy attachment](../../out/acceptance/rss/workers/xsk-udp.json)
passes all 8192 packets with exactly 4096 LAN and 4096 WAN descriptors on worker0;
worker1 is not polled. Worker0 can run from IRQ or AF_XDP wakeup contexts, so
fixed CPU placement is not claimed in this serialized fallback mode.

The [clean zero-copy wire test](../../out/acceptance/rss/workers/xsk-clean-wire.json)
sends 10 packets into hardware queue15, normally owned by worker1, and confirms
that only worker0 handles them. All 10 returned frames match the expected MAC
swap byte-for-byte. [Completion evidence](../../out/acceptance/rss/workers/xsk-clean-stop.json)
reports RX10, TX10, completions10, 1120 bytes and zero drop/invalid/ring-full
counts. The nonzero `tx_empty` wakeup statistic is not a failed descriptor.

An earlier echo attempt is retained as FAIL: the host already had IPv4
forwarding enabled, and an Ethernet-only echo addressed at the board's IP
looped through the host, visibly decrementing TTL. The corrected fixture uses
the host destination IP and does not alter host forwarding, routes or addresses.
The first accumulated 650 completions are not presented as the clean 10-packet
acceptance result.

**UMEM attachment/detachment reloads the original firmware.** Live readback
detects exactly two missing hash hooks; body SRAM remains present. The external
module was explicitly removed/reapplied after attachment for the test and
cleanly unloaded after detachment. The driver integration must reapply hashing
at the microcode reload point. A loaded external module alone is not evidence
that hashing remains active. General AF_XDP support on Linux queue1 is not added.

## Validation and recovered board state

[Independent audit](../../out/acceptance/rss/workers/audit.json): 40 checks PASS.
[Auditor source](../../port/mainline/rss-acceptance/audit-rx-workers.py) checks
per-interface worker ownership, CPU placement, sequence/byte results, XDP queue
identities, AF_XDP fallback, wire bytes and completion/restoration evidence.

The FIT build and focused W=1 driver/tag build pass; the
[W=1 log](../../out/acceptance/rss/workers/w1-build.log) has no compiler warnings
or errors. Full-file strict checkpatch has no errors, with two warnings and
four checks in pre-existing, unchanged IDM/netdev statements. New queue/header,
main-probe, conduit and tag changes have no checkpatch findings. Scoped
`git diff --check` passes. The extended XDP helper builds with
`-Wall -Wextra -Werror`; Python acceptance sources pass AST checks.

The board was freshly RAM-booted from the new accepted FIT, with the TFTP byte
count verified before bootm. After both carriers were confirmed up,
[recovery TCP](../../out/acceptance/rss/workers/recovery-incoming.json) passes
another 64 MiB per port without affinity writes; queue1/9 deltas are zero with
no hash module installed. [Final state](../../out/acceptance/rss/workers/recovery-final.json)
records RX2/TX2 on both conduits, taint0, only normal DSA modules, IRQ masks1/2,
XPS masks0, an empty kernel-error scan and 3/3 pings from each peer. No
flash/NAND/saveenv writes or host topology changes occurred.

The next gate is production microcode installation and ownership, including
UMEM/firmware reloads and controlled feature enable/disable. The bounded parser
and malformed-packet limits from [flow interaction](RSS_FLOW_INTERACTION.md)
remain in force. RSS feature advertisement and performance acceptance follow
that integration; they are not implied by the newly implemented two-worker RX.
