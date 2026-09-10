# TSO/TSO6 hardware feasibility audit

Subsequent RAM-only [template wire tests](TSO_TEMPLATE_WIRE_ACCEPTANCE.md)
validate multi-output template behavior and examine distinct payload data.
The static-only statements below describe this audit stage.

2026-09-09. Static audit of the available vendor kernel/modules, calibrated
PPU firmware and current Linux driver. No board state was changed.

## Decision

**There is a concrete hardware-template candidate, but no recovered general
TSO/TSO6 payload-segmentation contract that can be implemented yet.** The
factory producer is substantially more specific than a GSO function name:
it submits one short template with repetition/MSS/final-length fields.
However, it deliberately constructs zero payload and does not submit the
original skb's complete data. It cannot establish arbitrary TCP payload
segmentation, even if its template repetition works in hardware.

The SG blocker does not prove that continuous-buffer hardware segmentation is
physically impossible. That possibility remains unverified. Current Linux
integration additionally requires an SG-capable transmit interface and the
appropriate TX checksum features. Keep production TSO/TSO6 disabled.

## Primary evidence and reproducibility

* `plat_132.ko` SHA256:
  `6cb3e7c9567b4549b5d1b1c6d87f502d3b07fcde0461c2d28a5ad115661d4d51`.
* Saved vendor kernel `vendor-reference/2b5/kernel-2b5.elf` SHA256:
  `35941b131502ba785326692169dc3b9ca165d4417ee50632dc335e7a856baa6d`.
  Its `tcp_sendpage_upload`, `upload_hook` and `g_upload_driver_en` addresses
  agree with the archived runtime kallsyms. This is a static correspondence,
  not a newly observed running factory configuration.
* Intel PPU firmware SHA256:
  `056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40`.

Fresh decompilation records are under `out/acceptance/tso-audit/`, one JSON
per function. Descriptor writes and critical memset/memcpy calls are checked
against AArch64 instructions and ELF relocations, not pseudocode names alone.

```sh
python3 port/mainline/sg-acceptance/audit-tso-contract.py
```

The collector scans 28 distinct saved vendor module binaries, records relevant
symbols, fingerprints, key assembly and bounded PPU local-read candidates.
Its five consistency checks pass in `static-contract.json`; this is **not a
hardware acceptance PASS**. No additional named general segmentation/MSS API
was identified in the scanned modules. Stripped code, indirect calls and
undocumented hardware remain outside that negative symbol result.

## Three distinct vendor paths

| Path | Verified CPU work | Submitted source | Meaning for TSO |
| --- | --- | --- | --- |
| `net_tcp_gso_tx @ 0xf258` | Iterates payload, copies linear area/fragments into output nbufs, adjusts TCP sequence/IP length and ID, calculates or requests checksum | One contiguous nbuf and descriptor per output packet, `idm_flag=2` | Software segmentation; hardware checksum is a separate operation |
| `net_tcp_gso_tx_upload @ 0xec3c` | Computes header length/MSS, copies only headers, clears one template payload region, sets initial lengths/checksums | One template nbuf and descriptor, `idm_flag=3`, plus repetition parameters | Concrete template-generation candidate, not proof of normal payload TSO |
| `net_tcp_gso_tx_upload1 @ 0xef38` | Loops per MSS, allocates and zero-fills each output, copies headers, advances sequence in software | One template descriptor per output; calls upload-send with equal length/MSS | Software-generated zero-payload outputs; not an alternative arbitrary-data TSO path |

In the ordinary path, the payload-copy loop is at `0xf4dc..0xf594`, with
`memcpy` at `0xf500`; sequence advancement is visible at `0xf4a4/0xf4b4`,
IPv4 ID/length at `0xf43c..0xf468`, and last-output PSH restoration at
`0xf4b8..0xf4c8`. `net_hw_checksum` selects checksum handling after the CPU
already split and copied the data; it is not a hardware segmentation switch.
This audit does not certify the ordinary vendor routine for every packet type.

In upload mode, `0xedfc` resolves to **memset(data + H, 0, min(P,M))**;
`0xee38` resolves to **memcpy(data, skb_data, H)**. H is the total header
length, P the original payload length and M the selected MSS. The cached
64-template path is also initialized with zero data by `gso_upload_enable`
and only refreshes headers/clears superseded header bytes. There is no complete
payload copy or additional original-payload DMA address in these producers.

### Actual branch into the special upload path

`net_gso_tx @ 0xf87c` checks **skb byte offset 187, bit 4** at `0xf8d0`.
Without this flag it calls the ordinary software splitter. With the flag,
`gso_upload_mode` chooses upload or upload1. No normal Linux `gso_type` meaning
is assigned to that vendor-specific bit.

The saved kernel's `tcp_sendpage_upload @ 0xffffffc0107a2548` sets precisely
this flag: `ORR W0, W0, #0x10` at `0xffffffc0107a272c`, followed by the byte
store to skb+187 at `0xffffffc0107a2730`. `net_gso_init` enables the exported
upload support variable, connects `upload_hook` to `net_upload_fun`, and
creates `/proc/upload_ctl/upload`. This establishes a dedicated vendor upload
facility. Its name does not establish a general socket payload offload.

`idm_net_tx` calls `net_gso_tx` at `0xd2c8` and then frees the input skb at
`0xd2d0`. Its `idm_use_cpu_gso=0` alternate branch selects the separate
Wi-Fi producer, which again writes one pointer/length; that branch is not a
recovered Ethernet TSO API. WAN/LAN CPU paths also call `net_gso_tx` before
their ordinary descriptor setup. No hidden original skb data pointer was
found in `net_cfg_desc_by_skb`; it initializes source/output/control fields.

## Recovered descriptor producer fields

The following fields are verified writes in `net_gso_upload_send @ 0xe634`.
Offsets are byte offsets within its 32-byte CPU TX descriptor, with native
little-endian halfword stores. Semantic names below describe producer inputs,
not a decoded hardware consumer.

| Offset / bits | Producer value |
| --- | --- |
| +0, word0 | Physical address of the template nbuf's data |
| +4, word1 bits14:1 | 14-bit template frame length, H + min(P,M) |
| +27 bits3:2, word6 bits27:26 | `idm_flag = 3` |
| +18, word4 bits31:16 | For P > M: ceil(P/M) - 1 |
| +20, word5 bits15:0 | For P > M: P - (ceil(P/M) - 1) * M |
| +22, word5 bits31:16 | For P > M: M |
| words4/5, P <= M | Both words remain zero |

The divisions/stores are at `0xe6d4..0xe700`. For an exact multiple the
producer decrements the quotient and stores a full-MSS tail; it does not
encode a zero-length final output. With P=3000 and M=1460, the template has
only H+1460 bytes, word4 is `00020000`, and word5 is `05b40050`.
The original 3000 bytes are not present in that template.

This yields a specific replication-oriented hypothesis. It does not identify
a DMA cursor that advances through P distinct payload bytes, a source stride,
or an alternative descriptor mode for a complete large contiguous source.
The observed 14-bit frame-length field is not asserted to be the silicon's
absolute GSO-size limit; a different unverified encoding could exist.

## IPv4/IPv6 header and checksum boundary

The upload producer has IPv4 and IPv6 branches. IPv4 total length is written
at `0xee90`; IPv6 payload length at `0xeec8`. A special IPv6 next-header=4
branch handles an embedded IPv4 layout; it is not generic extension-header
support.

`net_gso_checksum_upload @ 0xe7f4`, called with its third argument set to 1,
sums only the TCP header while forming the pseudoheader checksum with the
full TCP segment length. `net_gso_ipv6tcp_checksum.constprop.6 @ 0xe788`
likewise sums only the TCP header before the IPv6 pseudoheader operation.
This is consistent with the deliberately zero-filled payload. It is not proof
of checksum generation over arbitrary distinct payload segments.

Initial header preparation is recovered. The **hardware-side** per-output
TCP sequence advancement, IPv4 ID/length updates, IPv6 length updates,
checksum adjustment, final FIN/PSH handling and ECN behavior are not recovered
or wire-validated for the template mode. IPv6 branches in a producer alone
do not establish Linux TSO6 compatibility.

## Completion and PPU consumer boundary

`cpu_net_nb_desc_tx @ 0xdfa8` stores one tagged nbuf owner and publishes one
descriptor. `net_check_tx_done_nolock @ 0xb4fc` advances one owner per 16-bit
completion-count delta and frees tagged nbufs through `cpu_net_free_nbuf`.
The latter skips actual freeing when nbuf byte44 bit1 is set, as it is for
the cached upload templates. A pool of 64 recycled templates must therefore
not be copied into a driver as a proven DMA lifetime policy.

The producer/consumer software suggests a descriptor-owned completion unit;
no new evidence establishes when template mode's hardware completion fires
relative to its final output or final payload read. Ordinary-TX completion
acceptance cannot silently be extended to multi-output template submission.

The existing calibrated PPU traces cover ordinary control value 2. They
locate the packet-word27 control read at PC1926 and a dependent branch at
PC1940, but do not establish the path for value 3. A bounded scan of CPU-path
PC1920..2219 identifies only previously calibrated local packet-word load
encodings. It does not decode MSS/repetition field consumption, external DMA
reads, loop completion or wire EOP. No trace from this audit claims those
missing consumers. Reusing the ordinary context's terminal PC as a template
completion event would be unjustified.

## Linux integration gates

Current `linux-6.18.38/net/core/dev.c:10812..10828` drops all TSO features if
SG is absent, and separately drops TSO/TSO6 if the corresponding IPv4/IPv6 or
generic TX checksum feature is absent. This is a Linux interface dependency,
not proof of a hardware scatterlist prerequisite.

The current ZTE TX path linearizes skb data and uses one `dma_map_single`;
it has no GSO descriptor construction. A future design could in principle
gather data into a contiguous buffer before genuine hardware segmentation,
but that would be a hybrid path with copying and its own ownership, failure,
size and performance validation. It would not solve hardware SG. Merely
setting SG/checksum/TSO bits to bypass core checks is not an implementation.

## Concrete next experiment, if continuing this candidate

The evidence supports a **bounded template-mode diagnostic**, rather than
production TSO code or an unknown-bit sweep:

1. Reproduce the exact non-pooled vendor flag3 template descriptor and zero
   payload for a one-output control, then a two-output exact-MSS case and a
   final-short case. Capture every Ethernet frame and verify headers, lengths,
   checksums and TCP flags at the peer. Keep sources allocated through recovery.
2. Correlate descriptor publication, all wire outputs, actual completion count
   and final source reads. Observe the value3 PPU branch and the actual reads
   of repeat/MSS/tail fields; do not infer them from software field names.
3. Only if those gates pass, distinguish replay of the same template payload
   from advancing reads through distinct payload ranges. A separate full-source
   contract is required before proposing normal TSO, followed by IPv6 coverage.

This audit does not execute that diagnostic. The zero-template candidate is
grounded; a full contiguous-payload descriptor variant is not yet recovered.
The practical result is **further reverse engineering is justified, but
general TSO/TSO6 implementation and feature advertisement are not ready**.

## Subsequent hardware evidence (2026-09-09)

The [template wire acceptance](TSO_TEMPLATE_WIRE_ACCEPTANCE.md) and
[PPU consumer trace](TSO_PPU_CONSUMER_TRACE.md) supersede the static-only board
state below. The template metadata loads, per-output TCP sequence/checksum
updates, and IPv4/IPv6 final-length branches are now observed in hardware.
Distinct A/B payloads still emit A/A, and descriptor completion precedes the
traced PPU output processing. General TSO/TSO6 remains blocked on payload
advancement, source ownership/completion, and SG; feature bits remain off.
The PPU report links the new evidence and final RAM recovery regressions.

## Work and board state at the original static audit

Added the report, raw static decompilation evidence and a reproducible collector.
No production code/firmware or kernel configuration changed; no module was
loaded, no board register was written, no factory boot or new network test
was performed, and no commit was made. The most recent verified board state
remains the preceding [RSS closure](RSS_CLOSURE_ACCEPTANCE.md); it was not
re-measured here.
