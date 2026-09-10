# RXHASH firmware fallback acceptance

Follow-up: `RSS_RECEIVE_PERFORMANCE.md` records subsequent controlled receive
throughput comparisons against both single-queue placements.

2026-09-09. The integrated RXHASH path passes bounded RAM firmware-mismatch
tests. This stage changes acceptance tooling, not the kernel implementation.

## Fault construction

The active RAM firmware initially matches SHA-256
`056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40`.
The fixture changes byte 56023 from 0x40 to 0x41 in that RAM file. This is the
last byte of PC7000's padding word, in the unloaded gap [5272,12208). It changes
the fingerprint without modifying any instruction loaded by the native firmware
path. The requested RSS change is WAN f0 -> 0f; its expected failure is errno95.
The original file is backed up and restored in a finally block.

For the one-read fault, the existing setter helper restores a valid copy after
one second, during the first hardware preparation's SerDes wait. For persistent
mismatch, both preparations see the mismatched fingerprint. After testing the
native fallback, the original file is restored and an XSK attach/detach cycle
forces full preparations with valid firmware again.

## Results

| Scenario | Verified result |
| --- | --- |
| One-read fault | Requested change rejected; one native preparation followed by valid rollback; generation 4 -> 5, active remains 1 afterward; both original f0 maps recovered |
| Post-rollback skb hashes | WAN and LAN each receive 128 exact UDP frames with the expected full skb hash; hardware-report counters increase |
| Persistent mismatch | Both preparations use native firmware; active becomes 0, successful-load counter remains 5, RXHASH feature switches remain on |
| Native fallback skb hashes | WAN and LAN each receive 128 exact UDP frames with skb hash zero and zero hardware-report counter increment |
| Restored firmware | XSK attach/detach installs valid programs twice, generation 5 -> 7; active returns to 1, both f0 maps recovered; both 128-frame hash checks pass |
| Final traffic | UDP 8192 packets and TCP 16 x 8 MiB integrity tests; IRQ affinity preserved |

The raw packet filter reads the skb hash directly, without software hash
generation, as established in `RSS_RXHASH_SKB_INTEGRATION.md`. Feature enablement
is deliberately distinguished from active calibrated hardware: an enabled
RXHASH feature does not cause metadata to be trusted during fingerprint fallback.

The first one-read run did not reach peer readiness before the traffic phase.
Its firmware restoration and generation increment were observed, but it is not
counted as packet acceptance. The fixture now waits for carrier plus a settling
interval before the existing peer-reachability gate, and emits partial error
reports after restoring the file. The completed rerun supplies the evidence.

The existing writable-RSS audit previously counted every program in the header,
so adding the CPU-submit export program invalidated its 416-word comparison.
It now explicitly compares only the two queue-selection hooks against the
historical hardware readback. All 77 existing configuration checks pass; the
third program remains covered by its separate export/integration evidence.

## Artifacts and limits

`out/acceptance/rss/rxhash-fallback/` contains the one-read and persistent records,
raw skb observations, before/after counters, firmware restoration hashes, queue
regression, and final traffic/state records. Run
`audit-rxhash-fallback.py out/acceptance/rss/rxhash-fallback` to verify them.

This establishes fingerprint-mismatch fallback and recovery for the tested
basic IPv4 UDP fixtures. It does not claim arbitrary corrupted-firmware safety,
MMIO failure recovery, or a new full AF_XDP echo test. The kernel and FIT are
unchanged from the RXHASH integration stage. IPv6/tunnel hash support and
performance gain remain outside the accepted scope; SG and TSO remain unchanged.
No flash/NAND/saveenv, host address/route change, or Git commit was performed.

All 35 fallback checks and all 77 queue-configuration regression checks pass.
Final taint is zero, RXHASH is on for both interfaces, active/load counters are
1/7, both peers respond to ping, and the kernel error scan is empty. Only normal
DSA modules remain loaded; no XSK, XDP helper or packet observer remains active.
The unchanged FIT is 11,003,260 bytes, SHA-256
`43bef302fe172abb1c7def61cdebd319ef60fde29131d8c614af6000a9c7893d`.
