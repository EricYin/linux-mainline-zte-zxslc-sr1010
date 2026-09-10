# RSS and RX multiqueue implementation

Latest metadata result: [PPU-to-DMA hash export](RSS_DMA_HASH_EXPORT.md) establishes
a complete 32-bit hash channel with 128 checks, including six exact-byte receive
runs. Export is still single-context diagnostic replay; continuous export,
skb RXHASH reporting and performance remain unfinished.

Latest production result: [writable RSS configuration](RSS_CONFIGURATION_ACCEPTANCE.md)
implements independent WAN/LAN tables, shared-engine reload persistence, rollback
and busy rejection for UMEM/hardware flows. The 77-check acceptance passes;
hardware skb hash reporting and performance remain open.

Latest hardware result: [programmable bucket-map prototype](RSS_PROGRAMMABLE_MAP_PROTOTYPE.md)
proves independent WAN/LAN hardware queue assignments with 188 checks, including
reversed/asymmetric per-flow evidence and fixed-worker placement. Production
setters, rollback and configuration persistence remain the next integration gate.

Latest standard interface: [RSS ethtool acceptance](RSS_ETHTOOL_ACCEPTANCE.md)
adds truthful read-only table/field queries on both conduits. Normal, UMEM,
disabled and recovery states pass 83 checks; configurable steering and hardware
hash reporting remain open. Earlier stage boundaries below are historical.

Latest result, 2026-09-08: [integrated hash lifecycle](RSS_HASH_LIFECYCLE_ACCEPTANCE.md)
closes automatic startup/UMEM hash installation with 53 passing checks and clean
default-on RAM recovery. Standard RSS configuration, skb hash reporting and
performance acceptance remain open. The older stage summaries below describe
their original boundaries.

Preceding driver result: [RX_WORKER_SPLIT_ACCEPTANCE.md](RX_WORKER_SPLIT_ACCEPTANCE.md)
implements RX2/TX2 on WAN/LAN conduit and splits each interface across both
workers/CPUs. Ordered UDP, 64MiB-per-port TCP, XDP queue identities and AF_XDP
single-worker fallback pass. Hashing still uses an external diagnostic and
needs integration into firmware reloads before production RSS advertisement.

Latest coexistence/lifecycle result: [RSS_FLOW_INTERACTION.md](RSS_FLOW_INTERACTION.md)
verifies a hardware UDP NAT flow with hash off/on/off, actual bypass traces,
live instruction readback and post-interface-cycle UDP/TCP integrity. The eight
malformed-frame comparisons retain explicit unmatched/truncated-packet limits.
The next driver step separates per-interface packet delivery from RX-worker ownership.

Preceding format result: [RSS_FORMAT_FALLBACK.md](RSS_FORMAT_FALLBACK.md) adds calibrated
IPv4/fragment/TCP/UDP guards to the continuous RAM module. Twenty-five format
traces and 375 independent checks pass, followed by 8192 ordered UDP packets,
64 MiB TCP per port and clean RAM recovery. Production integration, offloaded
flow/parser edge cases and per-interface RX-worker distribution remain unfinished.

Latest hardware result: [RSS_PPU_QUEUE_CONSUMER.md](RSS_PPU_QUEUE_CONSUMER.md)
locates the ordinary RX queue read at PC1558/1893 and validates single-context
WAN 8→9 / LAN 0→1 steering with actual ring counters. It precedes the continuous
hash diagnostic above; the production image and fixed selectors are unchanged.

Status, 2026-09-07: **ACTIVE; WAN/LAN independent RX NAPI implemented and
RAM-tested. Continuous hash diagnostic passes; production RSS is unfinished.** See
[RX_DUAL_NAPI_ACCEPTANCE.md](RX_DUAL_NAPI_ACCEPTANCE.md) for the latest running
data-path image, simultaneous 64 MiB per-port tests in each direction and recovery.
[RX_AFFINITY_ACCEPTANCE.md](RX_AFFINITY_ACCEPTANCE.md) records the latest image
and default CPU placement, including 64 MiB per-port incoming tests without
manual affinity writes.
The queue observation and steering results below describe earlier stages.
[RSS_HASH_PATH_AUDIT.md](RSS_HASH_PATH_AUDIT.md) resolves the MAC hash-mode
candidate to MAC/VLAN table keys and bounds the special flow queue field.
The user explicitly marked SG BLOCKED
and authorized moving to this stage. SG stays disabled. Existing WAN and LAN
two-ring TX support is retained.

## Recovered queue-selection contract

Factory `np.ko` `pro_table_set` (0xb2228) and `pro_table_get` (0xb2318)
access SDT 4 at `protocol + (port << 7)`. Their setters/getters, together with
`proto_table_test_get` (0xcfc60) and `proto_table_test_set` (0xcfdac), identify:

| Packed 16-bit entry | Meaning |
| --- | --- |
| bits 15:14 | action |
| bits 13:10 | `m_cpuq`, fixed CPU queue selector |
| bits 9:2 | policer bucket |
| bit 1 | limiter enable |

This is a fixed protocol-action selector, not an established flow-hash or
indirection-table interface. The current mainline `zx279133-np.c` writes 16
128-bit blocks per port starting at ERAM block `21266 + port * 16`.
Decoding all 128 raw halfwords gives WAN queue-field counts
`8:2, 11:7, 12:112, 13:2, 14:2, 15:3`, and LAN
`0:5, 2:7, 4:2, 5:4, 6:110`. These include inactive/reserved protocol entries
and are not traffic distribution predictions.

Factory `plat_132.ko` `cpu_net_rx` (0xc5dc) selects the hardware ring as
`queue + 8 * group`. `cpu_net_poll` (0xcce4) polls both groups through one NAPI.
`set_idm_int_cpu_rx_cpu_config` (0x139a4) moves the whole CPU RX interrupt's
affinity; it does not establish independent per-queue interrupt routing.
`idm_cfg_int` (0x14d88) assigns distinct CPU/Wi-Fi/release/local interrupts.

Two tempting API names do not supply RSS: `tm_cla_cpu_queueid_set`
(0xe7a0c) returns zero without doing anything; `tm_hash_mode_set` (0xe51b8)
calls `global_mac_hashmode_set`, updating one global MAC hash-mode bit. There
is no recovered connection from that bit to per-flow CPU queue selection.

## Driver change

Added `rx_hw_queue_0_descs` through `rx_hw_queue_15_descs` to WAN ethtool
statistics. These count descriptors consumed and released from the shared IDM
CPU RX rings, including LAN traffic, XDP actions and dropped descriptors.
They are deliberately named descriptor counters, not delivered-packet counters.
At that stage, the single RX writer and `rx_stats_sync` protected updates and
reads; the latest implementation uses two per-NAPI statistics blocks.
Statistics append after existing names; existing names and indices are retained.

The counters do not change queue selection, IRQ masks, NAPI ownership, page
allocation or advertised features. Linux still reports RX 1 / TX 2 on WAN and
LAN conduit. Hardware rings 16..23 are outside the ordinary CPU RX poll path
and are not included in this new statistic set.

## RAM acceptance

Evidence: [queue-baseline.json](../../out/acceptance/rss/queue-baseline.json).

New FIT: `out/sr1010-zxdbg.itb`, 10,997,312 bytes, SHA-256
`59afb36a18e6a51376ee68acd53a81f2336bdefe69392ad6028a5c5049653bde`.
Previous working FIT preserved as `out/sr1010-pre-rss.itb`, SHA-256
`65b0fa1174e37603d770b075446b9631ca367daa39a1e3af6812ea3894678d29`.

- Full build and focused ZTE `W=1` compilation passed. Strict checkpatch over
  the three modified driver files' accumulated diff: zero errors/warnings/checks.
  Kernel whitespace check passed. The full build emitted the existing missing
  host `depmod` warning; both DSA modules loaded successfully by explicit path.
- RAM reboot, TFTP transfer-length verification and boot succeeded. No flash
  or saved environment writes. Board is running the new FIT, without SG probes.
- WAN deterministic TCP: 8 flows, 16 MiB, zero bad bytes; TX rings drained
  708/708 and 11254/11254. RX delta 1671, mostly hardware queue 8 (1653).
- LAN deterministic TCP: 8 flows, 16 MiB, zero bad bytes; TX rings drained
  315/315 and 11393/11393. RX delta 996, mostly hardware queue 0 (976).
- WAN inbound TCP: 8 streams, 3 seconds, requested 2 Mbit/s per stream;
  receiver bytes 6291456. RX queue 8 increased 4409, queue 12 increased 18.
- WAN inbound UDP: same streams/duration/rate, 512-byte datagrams;
  receiver reported 11727 datagrams, zero lost. Queue 8 increased 11773,
  queue 12 increased 2, LAN queues 4 and 6 each increased 1.
- In all four runs, the sum of queue-counter deltas exactly equalled the RX
  release-counter delta. These windows include control/background traffic;
  they do not identify the queue of each individual flow.
- After TX acceptance, WAN/LAN pings passed 3/3; RX errors, invalid DMA,
  page lookup misses, refill deficit and TX timeouts were zero. Inbound tests
  likewise added no RX errors, invalid DMA, lookup misses or refill deficit.
  No BUG/Oops/WARNING/invalid-completion report was observed.

These are correctness and queue-observation tests, not throughput benchmarks.
RX checksum, XDP and AF_XDP were not separately reaccepted in this stage.

## Next implementation gate

The follow-up below resolves ordinary-data queue selection to the protocol 84
LOOKUP_MISS action. Next determine whether its fixed queue result can be replaced
or modified by a stable flow hash. Independent WAN/LAN NAPI and the required
shared-buffer synchronization are now implemented and accepted separately.
A protocol-only table edit cannot yet be called RSS.

The completed NAPI work resolved page pool, DMA ownership map, refill
producer/doorbell, refill deficit recovery, RX statistics, XSK pool and IRQ
re-enable ownership; see RX_DUAL_NAPI_ACCEPTANCE.md. The driver infers LAN versus
WAN from the queue group, so steering must preserve that distinction or replace
it with independently validated source metadata.

Acceptance of RSS requires varied independent flows, stable per-flow queue
assignment, meaningful distribution, no reorder/corruption, and validated
hash/indirection controls if advertised. Parallel RX additionally requires
evidence of separate NAPI execution and resource ownership under simultaneous
WAN/LAN traffic, ring reuse, link cycles, XDP and AF_XDP. Existing TX multiqueue
is not evidence for either of these missing RX capabilities.

## LOOKUP_MISS hardware steering: A/B/A completed

Factory `pro_str` (0xb2408) indexes 32-byte names at `pro_name` (0x1a6ef8).
Index 84 is `E_PRO_LOOKUP_MISS`; index 83 is `E_PRO_TCP_CTRL`.
`se_eram_table_get_by_sdt_info` (0x8a840) selects each 16-bit entry from
`data[(16 * index >> 5) & 3] >> (16 * index & 31)`. Therefore protocol 84
is the low halfword of word 2 in block 10 of the per-port table.

The ordinary-traffic concentration was not evidence that the protocol table
was bypassed. The earlier histogram counted all entries equally, while the
tested bulk traffic uses this specific reason. Its values explain queue 8/0:

| Direction | Original halfword | Temporary halfword | Hardware RX queue |
| --- | --- | --- | --- |
| WAN | `0x6030` | `0x6430` | 8 → 9 |
| LAN | `0x4014` | `0x4414` | 0 → 1 |

Only bit 10 changed; action and policer fields were preserved. Two literals in
`zx279133-np.c` were temporarily changed, built and loaded by a fresh RAM boot.
No live indirect-table writes or PPU instruction patches were used.

`tm_protocol_pktdeal_set` (0xe49e8) provides further source evidence: when its
queue-update flag is set, it writes the requested queue and adds 8 for WAN.
Thus the two eight-queue groups are source/direction groups in this path.
Older reverse-engineering notes that call the high group merely a jumbo group
must not be used to design RSS; descriptor jumbo flags are a separate field.

Evidence: [lookup-miss-steering.json](../../out/acceptance/rss/lookup-miss-steering.json).

| Test window | Temporary image | Restored image |
| --- | --- | --- |
| WAN inbound 8-stream TCP | q9 +4409, q8 +0; 6 MiB received | q8 +4412, q9 +0; 6 MiB received |
| WAN inbound 8-stream UDP | q9 +11770, q8 +0; zero UDP loss | q8 +11758, q9 +0; zero UDP loss |
| WAN 8-flow deterministic TX/ACK | 16 MiB, zero bad bytes; q9 +2408, q8 +0 | 16 MiB, zero bad bytes; q8 +2142, q9 +0 |
| LAN 8-flow deterministic TX/ACK | 16 MiB, zero bad bytes; q1 +1460, q0 +0 | 16 MiB, zero bad bytes; q0 +1560, q1 +0 |

Control and background packets also advanced other queues. This proves the
fixed selector controls the tested bulk RX traffic and that rings 9/1 are
usable. It does not identify every packet's reason, prove per-flow affinity,
or establish RSS. The large receive counts exceed ring depth, exercising reuse.

The new reproducible WAN fixture is
`port/mainline/network-acceptance/nic-rx-queues.py`. It runs eight TCP and UDP
streams, three seconds each, at a requested 2 Mbit/s per stream. It requires
at least 90 percent of WAN-group consumption on the expected queue, actual
received bytes, no new RX/DMA/map errors, no refill deficit, and zero UDP loss.
It deliberately does not require an atomic multi-counter snapshot or call its
aggregate observations a flow-affinity test. Both `--expected-queue 9` on the
probe image and `--expected-queue 8` on recovery passed; Python syntax passed.

Temporary literals are removed: `zx279133-np.c` has no remaining diff.
Both full builds succeeded; their existing missing host `depmod` warning did
not prevent explicit DSA module loading. Current restored FIT is 10,997,260
bytes, SHA-256 `393b484ad8bc08eac9419b6bb31967493e0e3476db127f0552d7c4784f8fd69b`.
Probe FIT is retained only as `out/sr1010-rss-lookup-miss-q9-q1.itb`, SHA-256
`37881a5846c0d16528013aeb44bd52869a1b24ddc9d509ec413772c03c24ce40`.
The earlier 10,997,312-byte image above is the first-stage baseline.

Final restored-image TX completions: WAN 815/815 and 11160/11160; LAN 650/650
and 11133/11133. Both peers passed 3/3 pings. Pending TX, TX timeouts, RX errors,
invalid DMA, page-map misses and refill deficit remained zero; no kernel
BUG/WARNING/Oops or invalid-completion report. RXCSUM remains on; SG and RX hash
remain off. Host addressing and flash were unchanged; no SG probe is active.
