# IPv6 RSS / multiqueue — 2026-09-12

## Implementation

The calibrated PPU programs now hash basic IPv6 TCP/UDP alongside IPv4.
WAN and the LAN conduit retain two Linux RX queues each, backed by hardware
rings 8..15 and 0..7 respectively. DSA user ports still expose RX queue zero;
the conduit performs the parallel receive work and the tagger preserves the
L4 skb hash. SG/TSO remain disabled and their investigation remains closed.

The IPv6 key includes all four words of each 128-bit address, both transport
ports and the parsed TCP/UDP protocol. It uses the existing fixed, keyless XOR
folding scheme, not Toeplitz. The eight-entry per-interface maps remain writable
through standard ethtool RSS operations. TCP_V6/UDP_V6 field queries report the
supported fields only when the calibrated IPv6 program is active.

`generate-ipv6-rss.py` produces the three reviewed instruction bodies at
PC6000 (268 words), PC6288 (276 words), and PC6592 (172 words). The firmware
fingerprint, body readback, body-before-hook publication and lifecycle locking
remain in force. `zx279133.rx_hash_ipv6=0` selects the previous IPv4 program;
`zx279133.rx_hash=0` disables the calibrated hash programs entirely.

## Output-offset correctness fix

An early LAN batch following RSS reconfiguration exposed a real error in the
first IPv6 implementation. Queue selection was correct and all bytes arrived,
but the exported hash was `ec27fbd6` instead of `a6993a16`.

Six classic-BPF snapshot-length observers independently read the existing skb
hash without invoking software hash generation. The erroneous value matched
recomputation with both header bases at the start of the Ethernet frame.
A breakpoint immediately before CPU submission then proved the cause:
SPR18 was `00020000` (both offset bytes zero), and normalization changed SPR6
from `00000080` to `00800080`. The first address load therefore read MAC bytes.
The ordinary counterpart had SPR18=`0002123a`, SPR6=`009200ba`, and the correct
IPv6 hash. IP/transport classification flags alone did not establish usable
header offsets on every CPU exception path.

The output program now requires both L3 and L4 offset bytes to be nonzero.
It preserves SPR10 while testing them, restores saved registers on rejection,
and clears the descriptor hash-valid marker before normal submission.
Hardware queue selection remains intact. The reproduced recovery-window
fixture now delivers all 64 exact bytestrings with zero skb hash, instead of
claiming an invalid hardware hash. Normal complete metadata still produces
the expected hash. The same output guard protects IPv4 in the combined program.

This is a supported fallback boundary, not loss of the receive queue feature.
Fragments, extension-header layouts rejected by the driver's bounded validator,
packets changed or processed by XDP, and CPU copies without header offsets
must not be presented as universally hardware-hashed skb traffic.

## Evidence and validation

Evidence is retained under `out/acceptance/rss/ipv6-calibration/` and
`out/acceptance/rss/ipv6-continuous/`. Initial WAN UDP/SYN/ACK replays verified
each address word and the complete calculated hash. The continuous tests use
actual ring counters, exact raw packet bytes and a classic-BPF `SKF_AD_RXHASH`
filter, which reads the skb hash without generating a software hash.

Each interface's matrix comprises 59 cases: eight source ports for UDP/SYN/ACK,
individual changes to all eight address words, three alternative maps across
eight flows, a wrong-hash negative filter, RXHASH off, and restoration.
The independently calculated expected ring preserves the native port group
and the low two hash bits while the selected bucket supplies the Linux queue bit.

The first matrix attempts are retained as negative evidence. These include a
serial JSON export failure, traffic sent before link recovery, and the actual
missing-offset bug above. Receivers now stop on the requested packet count;
statistics readers reject incomplete serial output. Descriptor-index association
is restricted to the first firmware generation; later tests use ring deltas and
the existing skb hash rather than pretending lifetime software counters are
fresh DMA-ring indices. Global hash counters allow only the separately bounded
non-fixture traffic when testing zero-hash delivery.

`branch16-positive.json` only reports a clean diagnostic replay. The proposed
SPR16 predicate encodings failed their semantic comparison and were discarded;
they are not used by the final program or counted as calibration acceptance.

The corrected WAN and LAN matrices pass all 118 cases and **764 independent
checks** (`guard-wan-matrix`, `guard-lan-steady`, `audit.json`). The earlier
recovery-window fixture separately proves exact delivery with zero skb hash
when output offsets are absent (`guard-early-fallback-verified.json`).

Existing link-local IPv6 addresses were used for real socket traffic. WAN and
LAN each pass 4096 UDP packets with no loss, bad bytes, duplicate or ordering
errors, plus eight TCP streams of 8 MiB each (128 MiB combined). Both RX queue
groups perform work. The first unprimed WAN UDP attempt missed the initial
packet of seven flows; it is retained separately. After IPv6 neighbor prewarm,
both interfaces pass (`wan-streams-warm.json`, `lan-streams.json`).

WAN XDP_PASS correctly suppresses hardware skb hash use and detachment restores
it. Shared UMEM reports Linux queue zero on both interfaces, rejects map writes
without reloading, preserves the expected WAN/LAN hash behavior, and restores
both maps after detachment. All 13 lifecycle checks pass
(`lifecycle-supported.json`). LAN conduit native XDP attachment remains
unsupported in the existing driver; the initial attempted attachment and
`EOPNOTSUPP` are recorded in `lifecycle.json`, not counted as a feature pass.

All six WAN/LAN extension-header, first-fragment and later-fragment fixtures
pass exact delivery with zero skb hash (384 packets total, `fallback-*.json`).
This does not claim a hardware skb hash for those layouts.

The final RAM boot uses the default IPv6 setting, without an explicit hash
command-line override. All six WAN/LAN UDP/SYN/ACK smoke tests pass. The updated
IPv4 UDP receiver verifies 8192 packets, and the updated TCP receiver verifies
16 x 8 MiB (128 MiB), with unchanged affinity. All **17 final-state checks pass**
in `final-state.json`: IPv6 enabled, both default maps `f0`, IRQ masks 1/2,
all RPS masks zero, hash active/load counters 1/1, no pending TX or driver errors,
taint zero, no probe modules/test processes and an empty kernel-fault scan.
SG/TSO/TSO6 remain off.

The final FIT is 11,004,196 bytes, SHA256
`ae938e099e33946df8605ed7b19671ab1ccf83e03854db4a78fb86d1dc40b900`.
The vendor firmware bytes remain SHA256
`056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40`.
Kernel/FIT and external diagnostic module builds pass; the generated header
matches its generator and the kernel patch passes checkpatch/whitespace checks.

## Reproduction

- `rss-ram-boot.py --mode default` reloads the current FIT in RAM without saving
  U-Boot environment. The IPv4 recovery FIT is `out/sr1010-rss-ipv4-recovery.itb`.
- `prepare-ipv6-rss.py` stages test tools and the existing RAM LAN configuration.
- `run-ipv6-rss-matrix.py --mode wan DIRECTORY` (or `lan`) runs and restores one
  interface's mapping and RXHASH state; `audit-ipv6-rss.py --wan DIR --lan DIR`
  verifies the complete saved matrices independently.
- `run-ipv6-streams.py --mode wan` uses existing link-local IPv6 addressing;
  LAN requires `--lan-scope` with the verified Windows interface index.
- `run-ipv6-fallback.py` checks exact extension/fragment fixtures and zero skb
  hash. The live test does not configure new IPv6 addresses on either host.

All board changes in this work are RAM-only. Unrelated bootinfo sources and
other staged repository changes are preserved. Local commits were requested
after acceptance; no push was requested.
