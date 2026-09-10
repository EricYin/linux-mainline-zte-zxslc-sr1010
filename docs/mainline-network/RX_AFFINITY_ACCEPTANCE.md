# Default WAN/LAN RX CPU placement

2026-09-07: independent RX NAPI now runs on separate CPUs by default on SR1010,
without an acceptance script first changing interrupt affinity. RSS flow hashing
remains unfinished. See [RSS_HASH_PATH_AUDIT.md](RSS_HASH_PATH_AUDIT.md) for the
additional factory-code audit; no guessed hash-mode switch was enabled.

## Change

`zx279133-main.c` calls `irq_set_affinity_and_hint()` after requesting the two
RX interrupts, using `cpumask_local_spread(0/1, dev_to_node(dev))`. This selects
online CPUs rather than assuming CPU1 always exists. A devm action clears both
hints before the requested interrupts are freed. The policy is applied once at
probe; ordinary netdev stop/start does not overwrite userspace affinity choices.
On the tested two-core board LAN is CPU0 and WAN is CPU1.

Removed `pin_eth_irqs_to_cpu1()` from the diagnostic initramfs. The first trial
proved why this was necessary: the driver hints were already 1/2, but that old
startup function wrote actual affinities back to 2/2. Merely reading the hint
would have produced a false acceptance. The final image verifies requested,
hint and effective masks together and checks poll CPU counters.

`nic-rx-payload-accept.py --keep-affinity` now tests existing placement without
writing affinity, including in its cleanup. Its JSON records the initial masks
and whether they were left untouched. Existing default fixture behavior remains
available for controlled manual-affinity tests.

## Exact-image acceptance

Current RAM FIT: `out/sr1010-zxdbg.itb`, also preserved as
`out/sr1010-rx-affinity.itb`, 10,997,748 bytes, SHA-256
`7d846707d82c215413db292df30648b0cf7909407705cf238879bfe5f1c36ca1`.

[Raw acceptance JSON](../../out/acceptance/rss/rx-affinity.json) contains startup
readback, byte checks, counter snapshots, override/link-cycle output and final
kernel state. The prior accepted data-path image remains
`out/sr1010-dual-napi.itb` (SHA-256 `2c15855ba7b477495bfc837c1a3f1b89c22eb0b37bff746f13379b18e63ad42b`).

- After fresh RAM boot, IRQ 22 requested/hint/effective masks were 1/1/1;
  IRQ 25 masks were 2/2/2. IRQ numbers are observations, not stable identifiers.
- With `--keep-affinity`, simultaneous incoming WAN/LAN traffic passed all
  sixteen streams, eight streams and 64 MiB per port, zero bad bytes.
  WAN NAPI recorded 3635 polls on CPU1 and none on CPU0, with 368 overlaps;
  LAN recorded 1802 polls on CPU0 and none on CPU1, with 234 overlaps.
  Foreign-pool pages were 21116/22955; buffer deficits and RX/DMA/map/refill
  error deltas were zero. TX submitted/completed deltas were 7633/7633 and
  4414/4414; no timeout occurred.
- Temporarily reversing the affinity to LAN CPU1/WAN CPU0, then bringing down
  LAN user port, LAN conduit and WAN and reopening them, preserved the user
  masks 2/1. Both peers passed 3/3 pings. Masks were then restored to 1/2.
- Following that complete link cycle, simultaneous outgoing WAN/LAN traffic
  passed eight streams and 16 MiB per port with zero bad bytes. Both TX rings
  drained; NAPI overlaps were 80/64. This outgoing fixture temporarily sets
  IRQ affinity and XPS, then restores them; it is a recovery regression, not
  the evidence for untouched startup placement.
- Final requested/hint/effective masks remain 1/1/1 and 2/2/2. XPS remains
  0/0 for each conduit. Both carriers are up; TX pending, RX errors, refill
  deficit and TX timeouts are zero. RX/TX checksum and GRO remain enabled,
  SG remains off, and no XDP fixture is attached. Kernel scan found no BUG,
  WARNING, Oops, call trace or NETDEV WATCHDOG.

Full build, focused driver W=1 build, strict checkpatch (0 errors/warnings/checks),
Python AST, init shell syntax and whitespace checks passed. Logs are
[full build](../../out/acceptance/rss/rx-affinity-build.log) and
[W=1](../../out/acceptance/rss/rx-affinity-w1.log). The W=1 run checks the same
final driver source; the subsequent image rebuild removes only the old init
affinity override. The existing host depmod warning remains.

TFTP transfer length was verified before bootm. One transfer exceeded the
helper's initial 30-second wait with retransmissions; it subsequently completed
and the exact byte count was checked before boot. No flash, NAND or saved U-Boot
environment writes occurred; host addressing and topology remain unchanged.

## Limits

This is initial CPU placement for the already implemented WAN/LAN NAPI groups,
not RSS. Each interface remains RX1/TX2, with no advertised flow hash or RSS
indirection controls. Throughput improvement has not been benchmarked. CPU
hotplug, irqbalance policy and driver unbind were not separately exercised;
normal interface lifecycle and explicit userspace overrides were tested.
XDP/AF_XDP data-path acceptance belongs to the preceding dual-NAPI report and
was not repeated for this affinity-only change.
