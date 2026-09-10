# PPU hash export to RX DMA metadata

2026-09-08: a bounded single-context replay now transfers the PPU's complete
32-bit flow hash into an RX DMA descriptor. Ten accepted exports match an
independent tuple-hash calculation. Six WAN/LAN UDP, TCP SYN and ACK runs also
verify every received frame byte and exact sender/receiver counts. The audit
passes 128 checks.

This resolves a concrete DMA metadata channel. Continuous export and Linux
`skb_set_hash()` integration are not implemented; `receive-hashing` remains
`off [fixed]`. The accepted production FIT and its
[writable RSS configuration](RSS_CONFIGURATION_ACCEPTANCE.md) remain unchanged.

## Baseline: the existing descriptor does not already report the hash

The driver clears only `desc->address` when releasing an RX slot. A read-only
userspace helper reads the configured descriptor base at IDM register
`0x19280008` and snapshots already-consumed slots through `/dev/mem`.
On a fresh boot with no engine reload, cumulative queue consumption modulo
2048 identifies the consumer index. Each snapshot is read twice and checked
for stability; the DMA address is zero in all accepted baseline slots.

Eight different source ports, sixteen packets per flow on each interface,
produce 256 stable descriptors. The eight expected hashes differ, but all
seven retained words are identical within each interface. This shows that the
current program does not carry those full hashes in retained descriptor fields
for these fixtures; it is not a universal statement about every hardware mode.

A separate RAM-only sentinel test stamps words 5..7 of the next available slot,
with unchanged consumption counters around the stamp. Subsequent reuse replaces
word 5 and writes zero to words 6/7. Thus the zeros are actual observed DMA
output, not merely untouched initialization. The WAN reused slot contains a
background packet rather than a uniquely identified payload frame; this test
establishes slot overwrite behavior only. Sentinel words were cleared afterward,
and final RAM reboot removed all diagnostic state.

## Recovered correspondence

The PPU's final local metadata and consumed descriptor match as follows:

| Descriptor word | Driver field | Observed source/meaning |
| --- | --- | --- |
| 0 | `address` | Payload DMA address, subsequently cleared by the driver. |
| 1 | `length_flags` | Length and parsed flags already used by the driver. |
| 2 | `metadata[0]` | PPU local word 15; existing LAN prefix handling uses its upper half. |
| 3 | `metadata[1]` | PPU local word 14. |
| 4 | `metadata[2]` | PPU local word 13; native LAN fixtures include VLAN information here. |
| 5 | `metadata[3]` | PPU local word 12; protocol/transport metadata. |
| 6..7 | `metadata[4..5]` | Rewritten as zero in the tested path; no useful export source established. |

Immediately before native submit PC2216, the diagnostic replays
`040000100000069b` to store SPR2 directly into local word 13. The host records
SPR2 for comparison but does not supply the hash value to that store: the
existing PPU program computed it from the packet.

The diagnostic separately sets bit 31 of local word 12 as a marker, preserving
all other bits. It saves/restores scratch SPR31 while doing so. Native PC2216
then submits the packet. Exactly one marked consumed descriptor appears in each
accepted export window, with `metadata[2]` equal to the complete hash and
`metadata[3]` equal to the original word 12 OR `0x80000000`.

Bit 31 is a diagnostic marker candidate, not a claim that vendor documentation
reserves it for RSS. A continuous implementation must establish its lifecycle
and format compatibility before relying on it as a general validity contract.

## Packet association and byte preservation

The trace records packet bytes and scalar state before/after export. Accepted
cases change only local words 12/13 across PC2216→2217; all other captured words,
including scalar registers and packet bytes, agree. Descriptor words 2..5 match
local words 15..12 exactly, and the hash predicts the actual hardware queue.
Additional source ports verify distinct complete hashes, not just three queue
selection bits.

Because local word 13 previously carries LAN metadata, packet preservation was
checked at the Linux receive interface as well. A bound AF_PACKET verifier
compares the complete original frame on `eth0` or DSA user port `lan1`.

| Format | WAN sent/verified | LAN sent/verified | Bad bytes / socket drops |
| --- | --- | --- | --- |
| UDP | 787 / 787 | 670 / 670 | 0 / 0 |
| TCP SYN, corrected marker operation | 780 / 780 | 650 / 650 | 0 / 0 |
| TCP ACK, corrected marker operation | 786 / 786 | 631 / 631 | 0 / 0 |

There is one exported packet in each run. Since every transmitted matching frame
is accounted for and byte-identical, the exported packet is included in that
integrity evidence. This is basic IPv4 fixture acceptance, not arbitrary VLAN,
tunnel or malformed-frame coverage.

The IPv4 first-fragment, LAN IPv6 UDP and WAN ICMP controls do not export a hash
and contain no marked descriptor in any scanned changed-queue window. These
controls also check that prior diagnostic markers are not mistaken for current
packet output. They do not exhaust all parser paths or prove all marker-lifetime
properties needed by a continuous implementation.

## Excluded attempts and corrections

The first WAN SYN raw capture reported 784 matching receives for 789 sends and
11 socket drops. It is retained as `excluded-raw-export-wan-ipv4-tcp.json` and
does not count as byte-integrity acceptance. The raw verifier now requests a
larger per-socket receive buffer; the accepted repeat has equal counts and zero
socket drops. No host topology or global network setting changed.

A trace review also found that SPR31 is not always identical to local word 12
on TCP paths. The initial diagnostic used SPR31 as the base for the marker and
therefore changed additional metadata bits. Those captures are retained under
`excluded-flag-operand/`. The corrected operation uses the original local word
12 and restores SPR31 afterward. Corrected SYN/ACK tests pass the stricter
metadata-preservation audit as well as byte checks. Earlier UDP captures already
satisfy that strict check and remain valid.

## Tools, build and recovery

- [Consumed-slot reader](../../port/mainline/rss-acceptance/rx-desc-read.c) and [baseline collector](../../port/mainline/rss-acceptance/run-rx-metadata.py).
- [PPU trace diagnostic](../../port/mainline/rss-acceptance/rx-ppu-trace.c), using `hash_export=1` only for one selected context.
- [Export/byte collector](../../port/mainline/rss-acceptance/run-rx-hash-export.py) and [raw byte verifier](../../port/mainline/rss-acceptance/rx-raw-verify.c).
- [Independent auditor](../../port/mainline/rss-acceptance/audit-rx-metadata.py), [128-check result](../../out/acceptance/rss/metadata/audit.json) and [raw evidence](../../out/acceptance/rss/metadata/).

Run `python3 port/mainline/rss-acceptance/audit-rx-metadata.py out/acceptance/rss/metadata`.
The module builds with W=1 and passes strict checkpatch. Userspace helpers compile
with `-Wall -Wextra -Werror`; kernel-only checkpatch preferences for volatile
MMIO/signal state and integer types do not apply to these userspace programs.
Python parsing and scoped whitespace checks pass.

After module unload and a fresh RAM boot, UDP 8192 packets and TCP 128 MiB pass.
Both peer pings pass, taint=0, active/load counters are 1/1, and the kernel error
scan is empty. `receive-hashing: off [fixed]` is recorded in final state.
The unchanged FIT is 11,001,964 bytes, SHA-256
`66bdbe1757b0aed66207269e1fc1afe61cb8ff1b32e6f5b47acf1c6d15de2878`.
No flash/NAND/saveenv, host address/route change or Git commit was performed.

Next, implement self-contained continuous export at a point that preserves
native metadata consumers and reliably distinguishes eligible packets. Then
read the marked descriptor in the driver, set the skb hash with the correct
type, and validate feature toggling, fallback formats, UMEM and engine reloads.
The current evidence does not yet justify enabling NETIF_F_RXHASH. Performance
acceptance remains outstanding.
