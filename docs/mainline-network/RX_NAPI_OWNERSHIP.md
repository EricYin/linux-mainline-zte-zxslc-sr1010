# RX NAPI ownership and interrupt routing

2026-09-07 latest: independent WAN/LAN RX NAPI is implemented and RAM-tested.
See [RX_DUAL_NAPI_ACCEPTANCE.md](RX_DUAL_NAPI_ACCEPTANCE.md) for the current
image, ownership changes, parallel traffic and recovery. RSS remains unfinished.
The interrupt acceptance and pre-implementation resource audit below are
historical evidence from the preceding single-NAPI image.

## Accepted interrupt split

IDM `+0x44` selects source 0 events, and `+0x50` selects source 2 events.
The current driver already requested both interrupts and masks them through
`+0x40`. Factory `idm_cfg_int` (plat_132.ko, 0x14d88) writes these routing
registers; `idm_int_enable` (0x13a78) clears selected bits in the interrupt mask.

Changed `ZX279133_IDM_LOCAL_MASK` from bit 9 to bits 15:8. The existing
complement expression makes `ZX279133_IDM_DIRECT_RX_MASK` bits 7:0. This routes
the known LAN CPU queue group to source 0 and WAN group to source 2, while both
handlers retain their existing single-NAPI scheduling. Live readback was:

| Register | Value | Group |
| --- | --- | --- |
| `0x19280044` | `0x000000ff` | LAN queues 0..7, IRQ 22 / GIC 33 |
| `0x19280050` | `0x0000ff00` | WAN queues 8..15, IRQ 25 / GIC 36 |

Linux IRQ numbers above are observations from this boot, not stable driver IDs.
The names still reflect the vendor source-2 name, `localtest`.

With IRQ 22 temporarily pinned to CPU0 and IRQ 25 to CPU1:

| Traffic | Integrity | IRQ 22 delta [CPU0, CPU1] | IRQ 25 delta [CPU0, CPU1] |
| --- | --- | --- | --- |
| WAN, 8 TCP flows | 16 MiB, zero bad bytes | [3, 0] | [0, 948] |
| LAN, 8 TCP flows | 16 MiB, zero bad bytes | [377, 0] | [0, 0] |

These interrupt snapshots bracket a slightly wider interval than the driver
counter snapshots; the extra WAN-window LAN interrupt is not a count mismatch
within one atomic snapshot. Background/control traffic is included.

This proves independent hardware routing and CPU interrupt affinity. NAPI
state still serializes the shared poller, so it is not evidence of concurrent
packet processing or a performance gain. Affinities were restored to their
original masks `2` and `2` after the test.

## Resource audit: why another poller cannot be added directly

The following findings are from the current kernel source, not inferred from
the number of hardware rings.

| Resource | Current owner and required change |
| --- | --- |
| `rx_cons[queue]` | Already one consumer cursor per ring. Assign disjoint groups to NAPI instances. |
| `rx_page_pool` | Allocation cache and direct recycle have one NAPI owner. Give each allocator its own pool or serialize every cache access and disable implicit direct recycling. |
| `rx_page_map` | Open-addressed ownership map; deletion moves later entries. Lock lookup/remove and insert as complete operations. Separate ring cursors do not make this map concurrent. |
| `rx_normal_bp`, `rx_bp_prod` | One hardware refill ring. Serialize slot reservation, ownership-map insertion, descriptor writes and publication. |
| Refill doorbell | Current poller publishes a local `refill` count after a batch. With two producers, one could publish past another producer's unfinished writes. Use a shared completed-prefix/pending count under the refill lock. |
| `rx_refill_deficit` and recovery | Missing-buffer credits are shared. Serialize deficit accounting and recovery reservations so two contexts cannot replace one missing buffer twice. |
| `rx_stats_sync` | Sequence counters do not serialize two writers. Use per-NAPI statistics and sum them in ethtool, or separately serialize updates. |
| `xsk_pool` | One device-wide UMEM and one XSK queue 0; allocation, TX work and pool replacement currently assume one poller. Preserve one-poller operation while XSK is active unless the hardware pool contract is expanded. |
| IRQ unmask | Each independent NAPI must re-enable only its own source mask. The current poller re-enables both groups together. |
| Lifecycle | Prepare, abort, stop, XSK replacement/rollback and removal must quiesce all consumers before resetting or freeing any shared state. |

Important source sites:

- `zx279133-idm.c`: `zx279133_idm_rx_remove_page`, `rx_take_buffer`,
  `rx_post_page`, `rx_post_xsk`, `rx_process_queue`, `rx_recover_refill`,
  and `rx_poll`.
- `zx279133-netdev.c`: shared IDM prepare/abort/stop and XSK pool cycle.
- `zx279133-main.c`: NAPI, page-pool and XDP memory registration/cleanup.
- `net/core/page_pool.c`: `page_pool_enable_direct_recycling` explicitly
  associates a pool with one NAPI and does not refcount multiple owners.

## Proposed next code change

Keep the verified WAN/LAN interrupt-group split. Separate pool allocation and
statistics ownership before scheduling a second NAPI, while retaining a short
lock for the hardware's common buffer map and refill ring.

The hardware obtains pages from one common supply ring; a page allocated by
the WAN poller can later arrive in the LAN group, and vice versa. Per-direction
pool allocation therefore does not imply per-direction page ownership.
Recycling and DMA synchronization must use the page's actual originating pool.
Never return a foreign-pool page through the current `page_pool_recycle_direct`
calls or an unconditional `xdp_return_frame_rx_napi` fast path.

The current kernel's generic XDP return path (`net/core/xdp.c::__xdp_return`)
obtains the actual pool from the netmem. Its `napi_direct` flag still allows
lockless caching, so the driver must account for cross-pool returns. Manual
pool registration/attachment APIs and paired cleanup are available in
`include/net/xdp.h`; the Intel libeth/idpf code provides local examples.
This is a design constraint, not a completed second-pool implementation.

Initially preserve serialized queue-0 AF_XDP operation during UMEM use; its
existing pool replacement path already resets the shared engine. Do not expose
extra Linux RX queues merely because there are now two interrupts. WAN/LAN
independent NAPI and per-interface multiqueue/RSS are separate acceptance gates.

Required validation for actual parallel RX: simultaneous WAN/LAN traffic,
per-NAPI CPU execution and overlap evidence, byte integrity, ring reuse,
refill exhaustion/recovery, link and shared-engine cycles, checksum, XDP and
AF_XDP regression. RSS additionally needs a recovered stable flow-hash selector.

## Regression evidence and final state

Machine-readable evidence: [irq-split.json](../../out/acceptance/rss/irq-split.json).

- Full RAM build, ZTE `W=1`, strict checkpatch and Python syntax checks passed.
  The full build retained the existing host `depmod` warning; DSA modules
  successfully loaded by explicit path.
- WAN inbound 8-stream TCP received 6 MiB. UDP received 11723 datagrams with
  zero loss. Hardware queues and the second IRQ advanced as expected.
- RX checksum on/off/extended/LAN/XDP_PASS suites all passed: 15/15/42/15/15
  received records respectively. The initial temporary wrapper had a NameError
  before testing; it was corrected before these successful runs.
- AF_XDP queue 0 received, echoed and completed exactly 10 frames / 1100 bytes.
  All ten captured Ethernet frames matched byte-for-byte after MAC swapping.
  RX/TX invalid descriptors, RX drops and refill deficit were zero. The fixture
  detached cleanly and the shared engine returned to ordinary page-pool RX.
- Interface down/up serial logs show WAN and LAN each passing 3/3 pings. The
  serial helper reported a timeout because a printk message split its start
  nonce; this was not a packet timeout. A separate bounded ping run also passed.
- After XSK and interface cycles, WAN/LAN each passed another 8-flow, 16 MiB
  integrity run with zero bad bytes. Completions were WAN 698/698 and
  11274/11274, LAN 614/614 and 11062/11062.
- Final RX errors, invalid DMA, ownership-map misses, refill deficit, pending
  TX and TX timeouts were zero. No BUG/WARNING/Oops/invalid-completion message.
  Both carrier values are 1; original IRQ affinities and GRO/RXCSUM restored.

Running RAM FIT: `out/sr1010-zxdbg.itb`, 10,997,660 bytes, SHA-256
`164df37661cf13b8e90b5a0c8d2b742e6e3c3bc0ad79334b0851d87219c60378`.
The same image is retained as `out/sr1010-rss-irq-split.itb`; the preceding
working image is `out/sr1010-rss-pre-irq-split.itb`. Hardware routing is kept
in source. LOOKUP_MISS selectors remain at original queues 8/0. SG/RX hash
remain off; no flash/environment or host-address changes were made.
