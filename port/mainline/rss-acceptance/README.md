# Ordinary RX PPU diagnostics

Current IPv6 implementation and evidence: [IPv6 RSS acceptance](../../../docs/mainline-network/RSS_IPV6_ACCEPTANCE.md).
`generate-ipv6-rss.py` emits the combined programs; `run-ipv6-rss-matrix.py`
and `audit-ipv6-rss.py` validate ring assignment, payloads and skb hash controls.
`run-ipv6-streams.py` adds IPv6 UDP order/TCP byte tests using existing link-local
addresses. `run-ipv6-lifecycle.py` covers WAN XDP and shared UMEM. Historical
IPv4-only and external-patch instructions below describe their dated stages.

Latest metadata diagnostic: [DMA hash export](../../../docs/mainline-network/RSS_DMA_HASH_EXPORT.md).
`run-rx-metadata.py --flow N` reads stable consumed slots on a fresh default-on
boot. `run-rx-hash-export.py --mode wan --kind ipv4-udp --raw` combines one
selected PPU export, marked descriptor lookup and exact raw packet verification.
Stage the matching trace module, `rx-desc-read` and `rx-raw-verify` in `/tmp`.
These probes are temporary; they do not enable Linux receive-hashing. Audit with
`audit-rx-metadata.py out/acceptance/rss/metadata`.

Current production interface: [writable RSS configuration](../../../docs/mainline-network/RSS_CONFIGURATION_ACCEPTANCE.md).
Use `run-rss-config.py` with no options for readback; `--wan 0xa5 --lan 0x3c`
applies explicit tables using `/tmp/rss-set`. Standard `ethtool -X DEV equal 2`
also works. Changes pause both interfaces and are rejected while shared UMEM
or hardware-offloaded flows exist. Same-map requests avoid reset.
`query-rss.py` contains write attempts from the earlier read-only acceptance;
on this image it can change configuration and is not a read-only collector.
Audit with `audit-rss-config.py out/acceptance/rss/config`.

Latest hardware diagnostic: [programmable maps](../../../docs/mainline-network/RSS_PROGRAMMABLE_MAP_PROTOTYPE.md).
With the native image (`zx279133.rx_hash=0`), `rx-ppu-hash.ko map_test=1`
accepts load-time masks `wan_map=0xf0 lan_map=0xf0`; bit N chooses Linux RX queue
0/1 for hash bucket N. This prototype uses PC6000..6203 and PC6288..6499.
It must not be loaded over the driver's enabled hash hooks. Map changes require
unload/reload between bounded runs; no live setter is provided. Use
`run-format-probe.py --rss-map-test` for the instruction calibration and
`audit-rss-map.py out/acceptance/rss/map` for the saved acceptance.

Latest interface: [standard RSS query acceptance](../../../docs/mainline-network/RSS_ETHTOOL_ACCEPTANCE.md).
Use `query-rss.py` to collect table/field queries and rejected writes on both
conduits. The interface reports the active fixed mapping, including all-zero
Linux queue mapping during shared UMEM; setters and hardware RXHASH remain absent.
Audit with `python3 port/mainline/rss-acceptance/audit-rss-query.py out/acceptance/rss/ethtool`.

Current image: [integrated lifecycle acceptance](../../../docs/mainline-network/RSS_HASH_LIFECYCLE_ACCEPTANCE.md).
Hashing installs automatically at startup and UMEM transitions. Inspect
`rx_hash_active` and `rx_hash_loads` with `ethtool -S eth0`; use boot parameter
`zx279133.rx_hash=0` for native queue comparison. Do not load the historical
`rx-ppu-hash.ko` into this enabled image; its compatibility guard rejects patched
hooks. External-module instructions below apply to historical/native images.
`run-udp-sequence.py` prewarms both selected peers before the measured window.
Audit current evidence with:

```sh
python3 port/mainline/rss-acceptance/audit-rx-hash-integration.py out/acceptance/rss/integrated
```

Latest result: [per-interface RX worker acceptance](../../../docs/mainline-network/RX_WORKER_SPLIT_ACCEPTANCE.md).
Use `run-udp-sequence.py --mode wan`, `--mode lan`, then the default dual-port
run to distinguish each interface's worker counters. The network acceptance
helper `nic-xdp-pass eth0 queues` counts actual XDP RX queue indices; stop it
with SIGTERM to print the counters and detach. `inject-trace-frame.py --count 10`
supports exact bounded wire fixtures in addition to the existing timed stream.
The saved worker evidence is audited with:

```sh
python3 port/mainline/rss-acceptance/audit-rx-workers.py out/acceptance/rss/workers
```

AF_XDP is still queue0-only. In the historical worker-only image, UMEM transitions reload firmware and remove
the external hash hooks: check live readback and reapply the module explicitly
only after a completed transition when a diagnostic requires hashing.
Earlier [flow coexistence and lifecycle acceptance](../../../docs/mainline-network/RSS_FLOW_INTERACTION.md):
The module status now reads actual hook/body SRAM; require `live_read=0` and
`mismatched_blocks=0` in addition to no stopped contexts after a lifecycle test.

`run-offload-echo.py` measures 128 deterministic request/reply pairs through the
existing two-direction `tc-udp-flow.sh` fixture, port 5301 and preference 38101.
It requires those two rules to be installed and uses the existing static TC
binary and `/tmp/ethtool-test`. Copy `flow-echo-probe.py` to the LAN peer's
temporary directory as `sr1010-flow-echo-probe.py`; the client uses its existing
Npcap device, while WAN uses an ordinary UDP socket. Run the collector before,
during and after hash installation, saving each JSON result separately.
`run-format-probe.py --flow-hit --mode wan` (or `lan`) traces the same installed
hardware flow. Its malformed `--kind` fixtures are observations; unmatched
traces must not be relabelled as proven hardware drops.

```sh
python3 port/mainline/rss-acceptance/audit-offload-interaction.py out/acceptance/rss/flow
```

The auditor consumes the separately saved records named in its source. Clean
up only the test's rules and qdiscs and finish with the normal RAM recovery.
Earlier [format fallback acceptance](../../../docs/mainline-network/RSS_FORMAT_FALLBACK.md):
`rx-ppu-hash.ko` installs self-contained temporary bodies at PC6000/6064 and hooks
consumers PC1558/1893. It verifies the loaded firmware and retains the original
SRAM words for unload. `audit_only=1` reads the unloaded gap and intentionally
returns EAGAIN without installing. Four calibrated predicates now restrict new
hash loads to parsed nonfragmented IPv4 TCP/UDP; other hook-reaching formats
execute the original queue instruction. This remains an isolated RAM diagnostic,
not production RSS.

`run-format-probe.py --mode wan --kind ipv6-udp` records a matched packet's full
path without injecting extra hash instructions through the debugger. With the
continuous module installed it observes that module's actual guard/body;
without it, it records a native reference. `--branch-test` calibrates predicates
by changing SPR10 on a selected context; `--branch-swap` tests exchanged branch
destinations. Use calibration only with the hash module unloaded. The auditor
excludes the retained candidate encodings which disproved a general inversion
rule. Save each JSON output separately, then run:

```sh
python3 port/mainline/rss-acceptance/audit-format-fallback.py out/acceptance/rss/formats
```

Build `udp-sequence.c` with the existing AArch64 builder using
`-O2 -Wall -Wextra -Werror -static`, placing the executable at `out/rss-udp-sequence`.
TFTP it to `/tmp/rss-udp-sequence` and make it executable. Copy
`udp-sequence-send.py` to the LAN peer's existing temporary directory as
`sr1010-udp-sequence-send.py`. The board must have the normal WAN/LAN fixture,
`/tmp/ethtool-test` and the external module in `/tmp`.

```sh
python3 port/mainline/rss-acceptance/run-udp-sequence.py --first 2 --flows 1 --count 128
# On the board: insmod /tmp/rx-ppu-hash.ko
python3 port/mainline/rss-acceptance/run-udp-sequence.py
python3 port/mainline/network-acceptance/nic-rx-payload-accept.py --keep-affinity
# On the board: rmmod rx_ppu_hash; require hooks/body restore=0 in dmesg.
python3 port/mainline/rss-acceptance/run-udp-sequence.py --first 2 --flows 1 --count 128
# Recheck the earlier saved unguarded continuous-*.json evidence:
python3 port/mainline/rss-acceptance/audit-continuous-hash.py out/acceptance/rss
```

Collect JSON stdout separately for each run; the auditor consumes the saved
`continuous-*.json` evidence named in its source. The UDP runner checks content,
per-flow order, loss, duplicates and driver errors; the auditor additionally
checks independently predicted ring deltas. Always finish with fresh accepted
RAM boot and normal byte-integrity acceptance. A failed module restore requires
RAM reboot before further packet tests.

Earlier [single-context prototype](../../../docs/mainline-network/RSS_PPU_HASH_PROTOTYPE.md):
`alu_test=1` calibrates register operations using known operands; it changes
arithmetic flags and is not a production packet-processing path. `hash_test=1`
computes a folded IPv4 tuple XOR in the matched context and feeds its low three
bits to the native queue consumer. Hash mode rejects queue overrides, ALU-test
combination, non-PC561 entry and unsupported packet formats.
`--late` adds `late_hash=1`, computing at the consumer using verified L3/L4
relative reads and SPR2/16 instead of host-restored scratch registers.

After loading the current module into `/tmp` on the board, run a bounded matrix:

```sh
python3 port/mainline/rss-acceptance/run-hash-probe.py --mode wan \
  --ports 48000,48001,48002,48003,48004,48005,48006,48007
python3 port/mainline/rss-acceptance/run-hash-probe.py --mode lan \
  --ports 48000,48001,48002,48003,48004,48005,48006,48007
python3 port/mainline/rss-acceptance/audit-hash-probes.py out/acceptance/rss
```

The LAN peer needs `inject-trace-frame.py` copied to its existing temporary
`sr1010-inject-trace-frame.py` path. Matrix output is JSON on stdout; the runner
does not overwrite prior host evidence. `--variant 1` changes IPv4 ID/payload;
`--proto 6 --variant 2` changes SYN to ACK plus sequence/ID/payload while keeping
the same tuple. All packet checksums are recomputed.

These are external RAM-only diagnostics, not part of the production driver or
initramfs. SG/WOE is not used. Read
[the consumer acceptance](../../../docs/mainline-network/RSS_PPU_QUEUE_CONSUMER.md)
before using the module. A trace briefly stops PPU contexts and may disturb
traffic. Use the isolated board fixture and retain the accepted RAM FIT.

Build against the configured kernel output with the existing builder:

```sh
docker run --rm -v /Volumes/code:/Volumes/code \
  -w /Volumes/code/zx279133 sr1010-stage15-builder \
  make -C linux-6.18.38 O=/Volumes/code/zx279133/out/kernel \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  M=/Volumes/code/zx279133/port/mainline/rss-acceptance W=1 modules
```

`rx-ppu-trace.ko` requires `frame=<hex>`: the complete expected PPU ingress
frame, 64..128 bytes and a multiple of four. The WAN fixture pads the existing
checksum generator's Ethernet frame with two zero bytes. LAN sends the same
untagged wire form, but its comparison adds `8100003e` between MAC addresses
and the original EtherType. Read the board MAC after each boot.

Start `inject-trace-frame.py --interface <pcap-device> --frame <wire-hex>` on
the connected peer, then load the board module while the three-second stream
is running. It matches all expected bytes; a missing match is a rejected trace.
The default `pc=561 steps=512` starts from the current ordinary Ethernet entry.
`queue=-1` is observational. Explicit `queue=9` for WAN or `queue=1` for LAN
replays one matched context's queue consumer; these values are diagnostic only.

Read `/sys/kernel/debug/rx-ppu-trace/trace` after module initialization returns.
`w0..63` are local packet words, `w64..95` are SPR0..31, and `w96..127` are
the response window. The first sample contains all words, later samples only
changes. Values are sampled before the row's instruction executes. The trace
records the actual replayed instruction when an override is requested.

Require result/restored/before/after all zero, a matched ME, and a nonempty
trace. Reaching the step limit is reported as an error, even though the module
attempts to resume the context. A failed restoration or remaining stopped
context requires RAM recovery before further traffic tests. Always unload the
module and stop any temporary trace-export server, then perform a fresh RAM
boot and normal data-integrity acceptance after a probe series.

Recheck the saved successful traces:

```sh
python3 port/mainline/rss-acceptance/audit-rx-trace.py out/mcode_intel.bin \
  out/acceptance/rss/ppu-wan-entry-561.txt \
  out/acceptance/rss/ppu-wan-tcp-syn.txt \
  out/acceptance/rss/ppu-lan-udp-tagged.txt \
  out/acceptance/rss/ppu-lan-tcp-syn.txt \
  out/acceptance/rss/ppu-wan-queue9-replay.txt \
  out/acceptance/rss/ppu-lan-queue1-replay.txt
```

`ppu-lan-udp.txt` is the intentionally retained unmatched comparison attempt,
and is not part of the successful auditor input set. The combined evidence JSON
also retains the unmatched PC10 attempt and the exact injected frame bytes.
