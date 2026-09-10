# RSS UDP packet-rate acceptance

Follow-up: [RSS_CLOSURE_ACCEPTANCE.md](RSS_CLOSURE_ACCEPTANCE.md) records the
RSS correctness matrix and direct observation of the LAN ordering concern.

2026-09-09. This stage measures UDP packets actually delivered to the receiving
application, offered rate, reported sequence loss, UDP socket-buffer errors and
hardware queue participation separately. It does not measure a raw DMA ceiling.

## Workload and controls

Eight IPv4 UDP streams run for 10 measured seconds after 2 seconds of warm-up.
Each formal configuration has three samples, with interleaved RSS maps. Explicit
source ports cover all eight calibrated hash buckets. The sender's actual rate
must be within 5% of its requested aggregate PPS for a sample to qualify.

Two data-frame sizes are distinguished:

* 64-byte UDP payload: 110-byte untagged Ethernet frame including FCS.
* 18-byte UDP payload: standard 64-byte untagged Ethernet frame including FCS.

Those sizes exclude preamble and inter-frame gap. LAN's internal DSA transport
tag is not part of the external peer's untagged frame size. TCP control traffic
for iperf is not counted as UDP application packets.

The image's normal application affinity is CPU0. IRQ masks remain 1/2 and RPS
remains zero throughout. The affinity-control trials launch only the temporary
iperf receiver with mask3; init, the console and other applications stay unchanged.
Default UDP receive-buffer size was observed as 212992 bytes; the existing
receive/send maxima are 4194304 bytes. Buffer-control trials request 1048576
bytes through iperf's per-socket `-w` option at both endpoints. The board's
server JSON reports an actual receive-buffer size of 2097152 bytes for those
trials. No global socket limits or host network configuration are modified.

RXHASH, RX checksum and GRO retain their existing enabled configuration. The
driver and firmware are unchanged. The runner restores each interface's original
f0 RSS map after its series and terminates its temporary port5203 server.

## Default-buffer observation

At WAN 100 kpps with 64-byte UDP payload and the normal CPU0 application
binding, single RX0 reports no sequence loss in three samples. Dual queues
report 0.0106%, 0.0131% and 1.6994% sequence loss; UDP RcvbufErrors also increase,
while the measured driver DMA/refill/drop/error counters remain zero.

Allowing the receiver to use both CPUs with the default socket buffers does
not by itself eliminate occasional loss. With both CPUs allowed and a 1 MiB
socket-buffer request, all six 100-kpps single/dual samples report zero sequence
loss and zero RcvbufErrors. This is evidence of an application/socket-buffer
constraint in the original workload, not a firmware correction. It also means
the default debug image must not be advertised as lossless at these PPS settings
without stating the application configuration.

UDP RcvbufErrors cover the whole collection window, including warm-up; iperf's
reported sequence loss covers its measured interval. The two counts are not
expected to be equal. The audit checks that UDP InErrors are classified as
RcvbufErrors rather than checksum or memory errors. It does not claim every
possible hardware loss source has an exposed counter.

## Accounting and reproducibility

Delivered PPS is computed from the receiver's own JSON as bytes / payload /
seconds. The audit checks byte divisibility, agreement between client-reported
and server-reported receiver counters, and received datagrams = expected sequence
packets minus reported losses. Offered PPS uses the sender's packet count and
duration. Sender and receiver interval boundaries differ slightly; tiny rate
differences at zero reported loss are not interpreted as packet creation/loss.

All recorded per-flow out-of-order counts are audited, as are actual hardware
queue counters and live hash/load generations. An audit PASS means the
measurement is internally consistent; a lossy measurement can be valid evidence.
It does not mean every tested configuration is lossless.

Formal raw data directories:

* `out/acceptance/rss/pps-wan-100k-stable`
* `out/acceptance/rss/pps-wan-100k-cpu3`
* `out/acceptance/rss/pps-wan-100k-buffer`
* `out/acceptance/rss/pps-wan-150k-buffer`
* `out/acceptance/rss/pps-wan-150k-minframe`
* `out/acceptance/rss/pps-lan-100k-minframe`

`pps-pilot` and the incomplete `pps-wan-100k` series are exploratory records,
excluded from formal summaries. The latter stopped on a client source-port
binding conflict and restored the RSS table; it is not a board/driver failure.
Subsequent series use lower explicit source-port ranges. The runner now saves
client error output if a future invocation fails.

Use `audit-rss-pps.py` with the six formal directories to reproduce the audit.
The receiver-affinity and socket-buffer controls are recorded in every relevant
trial; they must not be merged with default-profile results.

## Measured results and audit boundary

With receiver affinity mask3 and a 1 MiB socket-buffer request:

| Path / UDP payload | Offered PPS | RX0 delivered median | RX1 delivered median | Dual RX delivered median |
| --- | ---: | ---: | ---: | ---: |
| WAN / 64 bytes | 150000 | 138839 | 139617 | 150004 |
| WAN / 18 bytes (minimum frame) | 150000 | 137323 | not sampled | 150004 |
| LAN / 18 bytes (minimum frame) | 100000 | 99980 | 99988 | 99973 |

Each cell has three samples. WAN dual RX samples in this table have zero reported
sequence loss and zero UDP RcvbufErrors. LAN samples also report zero loss and
zero RcvbufErrors, but the receiver reports out-of-order events in single-ring
samples lan-0, lan-2 and lan-7. Their origin has not been localized; the client
summary alone does not expose these receiver events. Do not claim a clean LAN
ordering acceptance from these records.

The six-directory audit has **563 PASS / 3 FAIL out of 566 checks**. The three
failures are precisely those LAN ordering checks. They are retained, not waived.
The WAN-only formal series has 445 passing checks.

## 64-byte frame capacity investigation

The line-rate reference is 2500000000 / ((64 + 8 + 12) * 8) =
**3720238 packets/s**, including preamble/SFD and inter-frame gap. At 150000
packets/s the wire occupancy is only 100.8 Mbit/s, or 4.032% of 2.5 Gbit/s.
The earlier 150-kpps test was a successful offered load, not an established
receive ceiling. High throughput with large TCP packets cannot prove minimum
frame line rate.

All following probes use WAN, eight IPv4 UDP flows, 18-byte UDP payload, RSS f0,
IRQ affinity 1/2, RPS zero, receiver mask3 and a 1 MiB socket-buffer request.

### UDP application delivery

At 175 kpps a uniform-eight-bucket three-sample repeat delivers approximately
175000 packets/s in every sample. Reported sequence losses are 0, 26 and 0
packets (0%, 0.001486%, 0%), with zero RcvbufErrors and no reported reordering
in all three. The 26-packet discrepancy has not been localized; do not attribute
it to socket overflow or RSS without further evidence. This is a low-loss
operating point, but it does not pass a strict three-sample zero-loss criterion.
Raw records are in `pps-limit-175k-repeat`.

The 175/200-kpps repeat records pass all 82 measurement-consistency checks in
`audit-rss-pps.py`. This does not waive their recorded packet loss.

Consequently the highest tested point meeting that strict criterion remains
150 kpps. A precise zero-loss threshold between that point and 175 kpps has not
been measured, and longer-duration reliability has not been established.

At 200 kpps a first exploratory sample reports zero measured sequence loss,
but 2561 RcvbufErrors over the collection window including warm-up. A separate
three-sample repeat gives approximately 200009, 196735 and 194335 delivered
packets/s, with sequence loss 0%, 1.5915% and 2.8853%, and RcvbufErrors 0,
31864 and 68154. Both CPUs are approximately 95% busy. Thus 200 kpps is not a
repeatably lossless application operating point in this fixture.

Single overload probes at 225 and 250 kpps deliver about 153387 and 111991
packets/s, with 31.868% and 55.238% reported loss. UDP RcvbufErrors increase by
854002 and 1650741 respectively. Increasing offered load beyond saturation can
reduce useful delivery. These are workload-dependent application/stack limits,
not a demonstrated MAC or DMA ceiling.

Raw records: `pps-limit-200k`, `pps-limit-200k-repeat`, `pps-limit-225k` and
`pps-limit-250k` under `out/acceptance/rss`. The earlier `pps-limit-175k` probe
used a source base not divisible by eight and is excluded from uniform-eight-
bucket acceptance.

### Post-DMA driver processing, with selective XDP discard

`xdp-pps-sink.c` temporarily attaches a native XDP program that discards only
IPv4 UDP benchmark data addressed to port5203 with the chosen UDP length.
ARP, TCP control and UDP setup traffic pass normally. This exercises RSS,
DMA, driver polling and XDP, then deliberately discards packets before SKB and
UDP application delivery. It does not measure application goodput or bare MAC
capacity. The runner detaches the program and restores RSS on completion.

| Requested PPS | Actual measured sender PPS | All sender packets, including warm-up | Driver XDP discard count | CPU0 / CPU1 busy |
| ---: | ---: | ---: | ---: | ---: |
| 250000 | approximately 250000 | 3000006 | 3000006 | 31.4% / 32.2% |
| 500000 | 499997 | 6000032 | 6000032 | 56.2% / 57.1% |
| 1000000 | 641237 | 7704145 | 7704145 | 69.4% / 69.8% |

The all-interval sender packet total is used for the count comparison; its
window includes two warm-up seconds and ten measurement seconds. Deliberate
XDP drops are the successful processing counter here, not accidental losses.
No UDP errors occurred in these probes. Each row is a single exploratory sample.
Exact aggregate count agreement establishes a tested processing lower bound;
it is not a per-packet byte-integrity or long-duration reliability test.

The 1-Mpps request only produced about 641 kpps. This fixture therefore reaches
its generation limit before establishing the driver's ceiling. **At least
approximately 641 kpps has been processed in this short test; the actual
driver receive maximum remains unmeasured.** That rate occupies approximately
431 Mbit/s of a minimum-frame 2.5-Gbit/s link, around 17.2% of wire line rate.
CPU utilization alone must not be extrapolated into a promised maximum.

Raw records: `pps-sink-250k`, `pps-sink-500k`, `pps-sink-1m`. A faster verified
packet generator is needed to continue the driver-capacity sweep toward
3.72 Mpps; separate application profiling is needed to improve UDP delivery.

## Recovery

After the capacity probes, both RSS tables read back as f0 (00001111), RXHASH
is enabled and active, IRQ affinity remains 1/2, the console retains mask1,
and socket defaults/maxima remain 212992 / 4194304 / 4194304 bytes. No temporary
port5203 iperf server or XDP sink process remains. Each sink record contains
its detach marker. Kernel taint is zero, only the normal switch/tag modules
are loaded, and the checked dmesg fault signatures are absent.

Final WAN/LAN sequence validation passes all 8192 datagrams with zero missing,
bad, duplicate or out-of-order packets. Final TCP integrity validation passes
16 streams of 8 MiB (128 MiB total), with affinity unchanged. These are recovery
checks, not high-PPS acceptance. Records: `out/acceptance/rss/pps-udp-final.json`
and `out/acceptance/rss/pps-tcp-final.json`.

The FIT remains 11003260 bytes, SHA256
`43bef302fe172abb1c7def61cdebd319ef60fde29131d8c614af6000a9c7893d`.
This investigation changes test tooling and documentation only; the running
driver/firmware remains unchanged and no persistent board storage is written.
