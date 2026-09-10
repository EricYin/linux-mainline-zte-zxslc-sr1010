# RSS receive performance acceptance

2026-09-09. The two-queue configuration improves measured TCP receive throughput
over the better of both single-queue choices on the current RAM image. Median
improvement is **5.5% WAN** and **23.1% LAN**. This is a bounded host-to-board
TCP termination result, not a routed/NAT throughput claim.

## Measured results

Rates are receiver-side application Gbit/s. CPU columns are mean per-core busy
percentages over the broader observation window, including warm-up and client
startup/collection. They are not isolated driver CPU costs.

| Interface | RSS map / Linux queues | Samples | Median Gbit/s | Observed range | CPU0 busy | CPU1 busy |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| WAN | 00 / RX0 only | 3 | 2.228 | 2.223–2.229 | 53.8% | 96.1% |
| WAN | ff / RX1 only | 3 | 1.657 | 1.655–1.658 | 96.2% | 0.0% |
| WAN | f0 / RX0 + RX1 | 5 | 2.349 | 2.330–2.352 | 96.0% | 66.6% |
| LAN | 00 / RX0 only | 3 | 1.384 | 1.383–1.385 | 92.4% | 0.0% |
| LAN | ff / RX1 only | 3 | 1.827 | 1.675–1.831 | 42.7% | 92.4% |
| LAN | f0 / RX0 + RX1 | 3 | 2.249 | 2.246–2.253 | 92.6% | 69.0% |

The comparisons use the better single-queue median: WAN RX0 and LAN RX1.
Using means instead gives 5.4% WAN and 26.5% LAN. The slower 1.675-Gbit/s LAN
single-queue sample remains included; the median is reported prominently to
avoid overstating the gain from that variation. No confidence interval or
formal statistical significance is claimed from these small samples.

## Why single-queue placement matters

The existing initramfs pins init and application children to CPU0
(`port/mainline/initramfs/init`). Live shell affinity is mask1; LAN server-start
records also confirm mask1. That configuration was preserved.

With the existing IRQ masks 1/2, WAN RX0 runs on CPU1 and WAN RX1 on CPU0;
LAN RX0 runs on CPU0 and LAN RX1 on CPU1. Putting receive work and the iperf
application together on CPU0 produces a substantially worse single-queue
baseline. Both choices were therefore measured. The reported gain is relative
to the better choice, not the artificially larger gain over the worse one.
Two queues increase throughput here; the data do not establish lower total
CPU consumption, better latency, or performance with unpinned applications.

## Controlled workload and evidence

* Both external links are 2500 Mbit/s; MTU and host topology are unchanged.
* Eight TCP streams, 10 measured seconds after 2 omitted warm-up seconds.
  Source ports are explicit and vary between trials to avoid socket reuse.
  The audit recomputes all eight tuple buckets from actual connection records.
* WAN and LAN are tested separately, with their existing Mac and Windows peers.
  Both clients are iperf 3.19.1; the board is 3.21+. Comparisons are within each
  interface, not a claim that differences between the two host stacks are NIC cost.
* RXHASH, RX checksum and GRO remain enabled. RPS remains zero and IRQ masks
  remain 1/2. Only the selected interface's RSS table changes.
* WAN first uses 00/f0/f0/00/00/f0, then ff/f0/f0/ff/ff/f0. LAN uses
  00/f0/ff/ff/00/f0/f0/ff/00. Each sequence restores its original f0 map.
* The audit checks receiver accounting, unchanged affinities, active hardware
  hash counters, actual physical queue distribution and driver health. All
  measured driver error/drop/invalid-DMA/refill-shortfall/TX-timeout deltas are zero.

Raw records are in `out/acceptance/rss/performance/`, `performance-wan-q1/` and
`performance-lan/`. The shorter two-run WAN pilot in `performance-pilot/` is
excluded from the formal summaries.

One formal WAN dual-queue sample (`performance-wan-q1/wan-5.json`) has inconsistent
receiver summaries: the client reports 2,928,017,408 bytes and the server's own
JSON reports 2,927,886,336, a 131,072-byte difference. Its cause is not established.
The raw record is retained and explicitly excluded from throughput summaries;
the audit verifies the mismatch rather than silently accepting it. Both sets
of per-stream sums match their respective summaries. This is not called a
hardware corruption result. Retransmission counters are unavailable in the
client summaries and are not presented as zero.

Run the strict audit with that documented exclusion:

```sh
python3 port/mainline/rss-acceptance/audit-rss-performance.py \
  out/acceptance/rss/performance \
  out/acceptance/rss/performance-wan-q1 \
  out/acceptance/rss/performance-lan \
  --exclude-receiver-mismatch out/acceptance/rss/performance-wan-q1/wan-5.json
```

It passes 208 checks over the accepted records, hardware conditions and explicit
exclusion. A throughput audit PASS does not impose a minimum improvement: it
means the recorded workload and accounting meet the stated acceptance conditions.

## Recovery and remaining scope

Both RSS maps return to f0. The final deterministic UDP 8192-packet and TCP
128-MiB checks are separate byte-integrity regressions; iperf throughput itself
is not described as a deterministic payload verifier. Final state is recorded
in `performance/final.json`.

Both final integrity tests pass. Taint is zero, RXHASH is enabled on both
interfaces, active/load counters are 1/25, both peers respond to ping, and the
kernel error scan is empty. The temporary port5203 server is gone; the image's
normal default iperf server is left as it was. The FIT remains 11,003,260 bytes,
SHA-256 `43bef302fe172abb1c7def61cdebd319ef60fde29131d8c614af6000a9c7893d`.

No kernel code or FIT change was required. SG/TSO scope is unchanged. Small-packet
pps, latency, simultaneous WAN/LAN load, routed/NAT traffic and other application
CPU affinities remain separate workloads. No flash/NAND/saveenv operation, host
address/route/adapter change, or Git commit was performed.
