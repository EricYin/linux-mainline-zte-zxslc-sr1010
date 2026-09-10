# Programmable RSS bucket mapping: hardware prototype

Successor: [production configuration acceptance](RSS_CONFIGURATION_ACCEPTANCE.md)
integrates these programs, standard setters, reload preservation and rollback.
The temporary-module boundary below belongs to the preceding prototype stage.

2026-09-08: the PPU can now apply separately selected WAN/LAN bucket maps to
actual hardware RX queues in a continuous RAM diagnostic. Default, reversed,
fixed and asymmetric maps pass packet-integrity checks. Reversed and asymmetric
maps additionally pass all eight flows individually on both interfaces. The
independent audit passes 188 checks.

This is the hardware implementation evidence for a writable RSS interface.
The production driver still exposes the [read-only interface](RSS_ETHTOOL_ACCEPTANCE.md).
No ethtool setter or persistent map storage was added in this stage; the normal
FIT is unchanged. The temporary module was unloaded and the board recovered to
the accepted default-on image.

## Recovered operations and rejected hypothesis

The first calibration tried to generalize condition encodings directly to
SPR2 bits 0..2 and SPR16 bit 19. All 32 cases took the false target, including
inputs with the selected bit set. This hypothesis is rejected and retained in
[the original capture](../../out/acceptance/rss/map/calibration-rejected-direct-spr.json).
Its top-level PASS refers only to trace completion/restoration, not the rejected
predicate interpretation.

The successful path copies the three hash bits into SPR10 bits 4..6 and the
interface bit into SPR10 bit 3, then uses the already calibrated conditions
`2880`, `28a0`, `28c0`, and `2860`. All 32 input/branch cases pass. A partial
immediate write of queue bits to SPR16 uses
`0104200000000522 | (queue << 25)`: 32 cases, covering all eight values and four
different surrounding-bit patterns, preserve every bit outside 18:16.
The [successful calibration](../../out/acceptance/rss/map/calibration-spr10.json)
contains all 64 cases and confirms context release/restoration.

## Continuous program

The hash computation and IPv4/nonfragment/TCP-or-UDP guards remain unchanged.
After hashing, SPR16 temporarily saves SPR10. Hash bits are copied to the
temporary condition bits; the native interface bit comes from response word 32
bit 29, the same source used by the native queue consumer. A three-level branch
tree selects one of eight leaves in the appropriate interface's map.

Each leaf restores SPR10, executes the native queue read, replaces only its
three queue bits, and returns to the original continuation. SPR2 retains the
hash. Rejected formats take the original displaced instruction directly.
The two programs occupy PC6000..6203 and PC6288..6499 (204 and 212 words).
All 416 live words match the installed program, with no stopped contexts.

For bucket `b = hash & 7` and one-byte map `m`:

```text
Linux RX queue = (m >> b) & 1
hardware queue = native_interface_bit | (b & 3) | (Linux RX queue << 2)
```

Thus each Linux queue retains four physical rings. Two independent eight-bit
maps can represent arbitrary assignments of the eight buckets to the two Linux
queues; this run tests the configurations below, not every possible map pair.

| Configuration | WAN map | LAN map | Evidence |
| --- | --- | --- | --- |
| Default | f0 | f0 | 8192 ordered UDP packets; original bucket distribution. |
| Reversed | 0f | 0f | 8192 concurrent UDP packets plus eight separately measured flows per interface. |
| Fixed | ff | 00 | WAN q12..15 and LAN q0..3 gain 1024 each; q4..11 remain unchanged; worker 1 handles both interfaces' 4096 packets. |
| Asymmetric | a5 | 3c | Eight separately measured flows per interface; independent WAN/LAN ring predictions; 8192 concurrent UDP packets and 128 MiB TCP pass. |
| Restore default | f0 | f0 | Verified unload/reinstall, 8192 ordered UDP packets pass. |

For example, UDP source port 48000 to port 49000 selects bucket 3. Under the
reversed map it reaches LAN q7 / WAN q15; under the asymmetric maps it reaches
LAN q7 / WAN q11. This distinguishes actual interface-specific steering from
aggregate balance or merely changing software queue labels. Ring deltas can
include background traffic; the per-flow audit requires the expected ring's
payload count and excludes another ring carrying a competing full payload count.

## Register and fallback evidence

WAN/LAN UDP and TCP SYN traces traverse the new branches and select the expected
queues. Across the observed hook entry/return snapshots, all scalar registers
except SPR16 agree, including the temporarily used SPR10; packet words agree.
This is bounded trace evidence, not a complete ISA or unobserved flags audit.

The LAN IPv6 UDP fixture takes the original fallback and retains native q0.
The WAN ICMP fixture retains its native q13: unsupported formats must preserve
their actual native selector, which is not universally q0/q8. The tested first
IPv4 fragment bypasses both hooks entirely. Earlier malformed-packet limits
still apply; this stage does not claim a complete parser validator.

Each map transition occurs between bounded traffic runs by unloading and
reloading the temporary module. Hook/body restoration returns zero, native
firmware readback matches all 5352 loaded instructions before each installation,
and post-installation readback reports no mismatch or stopped context.
This is not acceptance of lossless live ethtool reconfiguration, failure
injection, or configuration persistence through UMEM/reset cycles.

## Artifacts, recovery and next implementation

- [Diagnostic program](../../port/mainline/rss-acceptance/rx-ppu-hash.c): `map_test=1`, `wan_map` and `lan_map` are load-time-only parameters; use only with the calibrated native/disabled image.
- [Calibration and trace runner](../../port/mainline/rss-acceptance/run-format-probe.py): `--rss-map-test` enables the new bounded calibration.
- [Independent auditor](../../port/mainline/rss-acceptance/audit-rss-map.py) and [188-check result](../../out/acceptance/rss/map/audit.json): recompute flow hashes/rings from fixture inputs and reconstruct sparse register snapshots.
- [Evidence directory](../../out/acceptance/rss/map/): calibration, map changes, individual flows, continuous traffic, seven traces, 416-word readback and final recovery.

Run `python3 port/mainline/rss-acceptance/audit-rss-map.py out/acceptance/rss/map`.
Both diagnostic modules build with `W=1`; strict checkpatch and Python parsing
pass. All work was RAM-only; no flash/NAND/saveenv, host topology change or Git
commit was performed.

The accepted FIT remains 10,997,816 bytes, SHA-256
`030a4a1380fb4c55f6d80b2cf4305ddb8e1ec99fb1d28c1f1deb06b00ca860b4`.
It was freshly RAM-booted without the disable override after all diagnostics
were unloaded. Recovery UDP 8192 packets and TCP 128 MiB pass, both peer pings
pass, taint is zero, IRQ masks remain 1/2, active hash/load counters are 1/1,
and the kernel error scan is empty.

Next, integrate a generated map program and per-interface configuration into
the driver, with a defined quiesce/restart boundary and rollback when installation
fails. Ethtool readback must track the accepted configuration, and UMEM/reset
reloads must reinstall it. Hardware skb hash reporting remains a separate
metadata-association task; SG/TSO remain disabled.
