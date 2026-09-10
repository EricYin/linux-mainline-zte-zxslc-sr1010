# WAN/LAN independent RX NAPI acceptance

Follow-up: [RX_AFFINITY_ACCEPTANCE.md](RX_AFFINITY_ACCEPTANCE.md) is the latest
running image and verifies separate CPU placement without manual affinity writes.
The image and default-affinity observations below describe the preceding stage.

2026-09-07: **implemented and RAM-tested**. WAN queues 8..15 and LAN queues
0..7 now have independent NAPI consumers. Controlled affinity tests demonstrate
simultaneous execution on the two CPUs and byte-correct traffic on both ports.
RSS flow hashing and multiple Linux RX queues per interface remain unfinished;
each interface still reports RX 1 / TX 2. SG remains BLOCKED and disabled.

## Current image and evidence

Running FIT: `out/sr1010-zxdbg.itb`, also saved as `out/sr1010-dual-napi.itb`.
Size: 10,998,132 bytes. SHA-256:
`2c15855ba7b477495bfc837c1a3f1b89c22eb0b37bff746f13379b18e63ad42b`.
Loaded with TFTP and bootm into RAM; no flash or saved environment writes.

[Machine-readable evidence](../../out/acceptance/rss/dual-napi.json) includes
both traffic directions, checksum cases, wire-checked XDP_TX and final state.
[Build log](../../out/acceptance/rss/dual-napi-build.log) and
[W=1 log](../../out/acceptance/rss/dual-napi-w1.log) accompany it.

## Implementation and ownership

- Source 2 schedules WAN NAPI; source 0 schedules LAN NAPI. Each polls disjoint
  hardware rings and unmasks only its own interrupt group after completion.
- Each NAPI has its own page pool, cursor and sequence-protected RX statistics.
  Existing ethtool counters aggregate both writers, preserving prior indices.
  Ten appended counters expose each NAPI's polls, CPU placement, overlap and
  pages received from the other allocator.
- Hardware still shares one refill ring and DMA ownership map. A spinlock
  serializes complete map operations, supply-slot insertion and producer advance.
  A shared pending count publishes only fully written slots, after a DMA barrier.
  Deficit accounting and recovery reservations use the same lock.
- Hardware can return either allocator's pages to either RX group. Recycling
  and DMA synchronization use the page's actual pool. Cross-pool direct recycling
  is disabled; XDP_TX recognizes both pools on the shared DMA device.
- AF_XDP retains its existing single UMEM/queue-0 contract: while attached, both
  IRQs schedule WAN NAPI, which handles all ordinary RX rings. Pool replacement,
  stop and rollback quiesce both consumers before resetting shared state.

## Acceptance on the current image

Tests temporarily set LAN IRQ affinity to CPU0 and WAN to CPU1. Outbound tests
also set the two TX queues' XPS masks to CPU0/CPU1. These settings are restored
by the fixtures. Background packets are included in driver counter intervals.

| Test | Byte integrity | WAN NAPI CPU1 polls / overlaps | LAN NAPI CPU0 polls / overlaps |
| --- | --- | --- | --- |
| Simultaneous peers → board | 8 streams and 64 MiB per port, all 16 streams PASS | 3858 / 554 | 5060 / 627 |
| Simultaneous board → peers | 8 streams and 64 MiB per port, all 16 streams PASS | 4693 / 141 | 1127 / 79 |

Inbound foreign-pool page counts were 21601 WAN and 23489 LAN; outbound counts
were 3058 and 2299. This exercises the shared supply ring across both allocators.
There were no RX errors, invalid DMA addresses, ownership lookup misses or
refill shortfalls. Deficit remained zero. Outbound TX queues submitted/completed
54534/54534 and 40051/40051 descriptors, with zero pending descriptors.
Hardware TX checksum increased by 94579 during the outbound run.

RX checksum fixtures `on`, `off`, `extended`, `lan`, `xdp` all passed on this
exact FIT: 15, 15, 42, 15 and 15 accepted packets respectively, with expected
checksum-valid metadata and malformed-packet filtering. Native XDP_TX returned
10 exact 110-byte Ethernet frames after MAC exchange. This wire test used an
ephemeral capture; the saved JSON contains the expected bytes and match counts.

The preceding dual-NAPI trial (SHA-256
`32ff5cd7d5968ed13287324f29331116f8d96826d182f1d60078a895f4129894`)
also passed AF_XDP zero-copy: 10 RX, 10 TX, 10 completions, 1100 bytes, zero
invalid/drop counters, and LAN NAPI remained idle while the WAN poller handled
the UMEM. After detach, parallel traffic passed again. Its 512-frame native
XDP_TX test also passed. These results are retained separately in the JSON;
AF_XDP was not repeated after the subsequent TX padding fix.

Full build and focused W=1 compilation passed. Strict checkpatch over the four
driver files' accumulated diff reported 0 errors, 0 warnings and 0 checks
(2048 lines). Whitespace and Python AST checks passed; the static C receiver
compiled with `-Wall -Wextra -Werror`. The full build retained the existing
missing host depmod warning; required board modules loaded by explicit path.

## LAN short-frame TX checksum correction

Initial large inbound LAN tests stalled on both the prior single-NAPI image
and the first dual-NAPI image. With LAN TX checksum disabled, the same 64 MiB
test passed. Small response tests showed all board-side bytes verified while
the peer failed to receive the final two-byte response.

The DSA tagger pads short Ethernet frames before inserting its tag. The conduit
driver's old minimum-frame-length check therefore admitted padded TCP ACKs and
small responses to hardware checksum. The driver now requires the bytes from
the IP header to DMA-frame end to equal IPv4 `tot_len`; trailing Ethernet padding
selects software checksum. This is a narrow fallback: larger unpadded IPv4 TCP
frames retain hardware checksum.

After this change, with TX checksum enabled, simultaneous inbound WAN/LAN
64 MiB per port passed in full. Evidence is source analysis, feature A/B and
the fixed-image repeat; no raw wire checksum comparison was captured for the
failing padded response. The earlier LAN stall is not an outstanding NAPI blocker.

## Recovery and remaining work

Both interfaces survived down/up, then WAN and LAN each passed 3/3 pings.
XDP was detached. RX checksum, TX checksum and GRO are enabled. IRQ masks
are restored to `2,2`, XPS to `0,0` on each conduit, both carriers are up,
and the kernel scan found no BUG, WARNING, Oops, call trace or watchdog timeout.
Host addresses and topology are unchanged. Board remains on the current RAM FIT.

Default affinities currently put both IRQs on CPU1; automatic CPU distribution
has not been added. Parallel execution requires separate IRQ affinities as used
above. This acceptance proves concurrency and data integrity, not a throughput
gain. Allocation exhaustion and failed pool-replacement rollback were not fault
injected. Each interface still has one Linux RX queue; these are WAN/LAN groups,
not two RSS queues for one interface.

Next work is to establish a real per-flow hardware queue selector and stable
same-flow ordering before exposing RX hash, RSS indirection or ethtool channels.
The recovered protocol table permits fixed steering, but does not establish a
flow hash. Independent NAPI removes the software serialization prerequisite;
it does not supply that missing hardware contract.
