# RSS correctness closure — 2026-09-09

This is one continuous closure run following the small-packet capacity
investigation. It does not repeat the PPS ceiling sweep. Production support
remains two Linux RX queues with independently configurable eight-bucket WAN
and LAN maps, and guarded IPv4 TCP/UDP hardware hash reporting. IPv6 hardware
RSS, SG and TSO are not added by this work.

## Scope and evidence

The serial fixture runs `run-rss-closure.py` sequentially, preserving the
existing host topology and restoring the initial RSS maps, RXHASH switches and
IRQ/RPS affinity. Each step writes a separate raw record beneath
`out/acceptance/rss/closure` and `closure-tail`; independent validation is provided by
`audit-rss-closure.py`. A runner exit alone is not the acceptance criterion.

The matrix includes five WAN/LAN map pairs (00/ff, a5/3c, ff/00, 5a/c3 and
f0/f0), ordered byte-checked UDP traffic after every change, unchanged-map
writes, sixteen individually measured WAN/LAN flows under a5/3c, a WAN/LAN
port down/up cycle, both-port RXHASH disable/re-enable, shared UMEM attachment
and removal, one-read firmware fingerprint failure, persistent fingerprint
failure with native receive fallback, valid-firmware recovery, and final
UDP/TCP integrity checks.

The packet-format matrix sends 128 exact packets per format and interface:
basic IPv4 UDP, TCP SYN, TCP ACK, IPv4 options, first IPv4 fragment and basic
IPv6 UDP. A socket filter reads `skb->hash` directly. The first three formats
require the computed hardware hash; the latter three require zero hardware
hash while still delivering the exact frame. This is not an acceptance of
arbitrary malformed packets, encapsulations or IPv6 hardware hashing.

## LAN ordering investigation

The preceding PPS dataset retains three receiver-reported ordering failures.
They occur in single-ring trials. They have not been reclassified as successful
RSS acceptance merely because a later trial passes.

The temporary `udp-order-observe.c` helper attaches an AF_PACKET socket to
`lan1`, filters only 18-byte iperf UDP data addressed to port5203, and records
per-flow sequence gaps, backwards values, equal-to-highest values, and socket
capture drops. It does not modify delivery. Its workload adds capture overhead
and is not interchangeable with the preceding capacity measurements.

In `closure-order`, completed trials 0 and 3 captured 1198919 and 1199021
packets respectively, with zero capture drops, gaps, backwards or equal
sequences. Trials 1 and 2 had observer socket drops (1387 and 68); these are
incomplete captures and cannot establish absence of ordering anomalies.
All four completed iperf receiver summaries report zero ordering events and
zero loss. The fifth client timed out after 35 seconds. The runner restored
RSS and stopped the board-side processes; its two exact-source-port Windows
processes were then terminated. The partial trial is retained as
`closure-order/lan-4-timeout.json`, excluded from acceptance.

iperf's receiver counts both backwards and equal sequence values as
out-of-order, so its aggregate counter cannot distinguish reordering from
duplication. See the [iperf 3.21 UDP receiver implementation](https://github.com/esnet/iperf/blob/3.21/src/iperf_udp.c).
These new captures have not reproduced the earlier events and therefore do
not localize their origin. The old three failures remain an explicit unresolved
boundary; neither an RSS defect nor a sender defect has been established.

An additional bounded replay uses the exact source-port bases of original
trials 0, 2 and 7: 46000, 46064 and 46224, with their original single-ring
maps. The observer first passes a five-packet self-check with the injected
sequence 1,3,2,3,4: exactly one backwards event, one equal event and one gap.
All three replay clients complete successfully with zero receiver-reported
loss and zero ordering events. Replay 0 has 8044 observer socket drops and is
not a complete trace. Replays 2 and 7 capture 1199701 and 1198895 packets,
respectively, with no gaps, backwards/equal values or capture drops; both
totals exactly equal the sender's all-interval packet counts. These are direct
negative reproductions of the old issue, not proof that the five historical
events never occurred. Raw records are in `closure-order-replay`.

## Implementation review

No production driver or firmware change was indicated by the passing closure
checks. Source review confirms that RSS queries/changes serialize on the
datapath mutex, unchanged maps return before reset, active hardware flows
reject changed maps, and shared UMEM exposes the effective queue-zero state.
The reviewed firmware loader writes and reads back all program bodies before
installing hooks, publishes hash activity only after preparation, and clears
it during teardown. Receive annotations require the valid metadata marker,
the enabled feature and the bounded L4 validation; packets that ran XDP do not
reuse the earlier hardware hash.

Changes in this closure are confined to acceptance tools: batch orchestration
and audit, direct IPv6/fragment fallback observation, the packet-order observer,
and recording/cleanup of timed-out Windows iperf clients. The raw verifier's
IPv6 extension is limited to basic IPv6 UDP. Both C helpers compile with
`-Wall -Wextra -Werror`. Historical configuration and skb-hash audits were also
rerun against their recorded evidence: 77 and 48 checks pass respectively;
these are historical evidence audits, not new board runs of every old fixture.

## Final outcome

The independent closure audit passes **166 checks**, including actual payload,
raw skb hash and per-flow hardware queue evidence. Reproduce it with:

```sh
python3 port/mainline/rss-acceptance/audit-rss-closure.py \
  out/acceptance/rss/closure --continuation out/acceptance/rss/closure-tail
```

The first collection incorrectly expected zero hash on LAN while XDP was
attached only to WAN. It received zero packets through the zero-hash filter,
while driver statistics showed 128 hardware hash annotations. This was a
fixture expectation error: shared UMEM changes queue selection, but only the
interface that executes XDP suppresses its earlier hash. The original failed
record remains in `closure/umem-pass-lan-ipv4-udp.json`. `closure-tail` repeats
the UMEM and remaining lifecycle stages with WAN expecting zero and LAN
expecting the computed hash; both pass. The audit explicitly selects those
continuation records without erasing the failed attempt.

One-read and persistent fingerprint failure both return the expected error,
retain connectivity in their documented active/native modes, restore the RAM
firmware checksum and recover full hardware hashing. Final closure checks
pass 8192 UDP sequence packets and 128 MiB TCP with intact payloads, original
RSS maps and affinities, active hashing, zero kernel taint and no checked
kernel fault signatures. The later ordering replay has its own final recovery
records so these closure checks are not mistaken for the last action on-board.

Small-packet capacity remains separately documented in
[RSS_UDP_PPS_ACCEPTANCE.md](RSS_UDP_PPS_ACCEPTANCE.md).

The entire batch, corrected lifecycle continuation and three targeted ordering
replays have finished. The final replay recovery again passes 8192 ordered
UDP datagrams and 128 MiB of TCP data. Both RSS maps are f0, both RXHASH
features are enabled, activity/load is 1/90, IRQ masks remain 1/2, the console
is still pinned to CPU0, and global socket-buffer values are unchanged.
Temporary board observer/XSK/port5203 server processes and exact-source-port
Windows replay clients are absent. Kernel taint is zero and the checked fault
scan is empty. See `out/acceptance/rss/closure-final-state.json`.

The FIT is unchanged: 11003260 bytes, SHA256
`43bef302fe172abb1c7def61cdebd319ef60fde29131d8c614af6000a9c7893d`.
No production kernel rebuild was needed because only test tools and documents
changed. No flash/NAND/saveenv, host topology change or Git commit occurred.

**Disposition:** the bounded RSS functionality passes this correctness matrix;
the historical LAN ordering anomaly remains unlocalized despite targeted
negative reproductions. It is not valid to call the entire historical test
record failure-free or to claim an absolute small-packet receive ceiling.
