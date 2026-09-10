# SG wire split tests, 2026-09-07

Status: **SG is not implemented. The tested multi-descriptor WOE paths do
not preserve the two payload ranges as one correct Ethernet frame.**

This continuation combines the working WOE whole-frame egress control with
two noncontiguous 64-byte source ranges. All six captures contain the exact
128-byte control frame and both ping-control replies. None contains the
expected 128-byte frame made from the two ranges.

## Fixture and acceptance

The source addresses are `9bc62000` and `9bc63000`, 4 KiB apart.
Only 64 bytes of the split packet are initialized at each address; adjacent
arena bytes are zero. The reference packet uses a different seed from the
whole-frame control. The host independently constructs both expected frames.

The fixture first submits one whole-frame descriptor, recognizes its exact
payload at CPU-input PC1924 and verifies the metadata repair. It then exposes
both fragment descriptors with one producer-pointer update from 1 to 3.
It does not wait for the first fragment before submitting the second.

The whole-frame wire control is checked in the same capture after the run;
a missing control invalidates interpretation of the split result. The
internal control gate is a payload/metadata check, not a substitute for the
peer's wire result. All six runs passed the peer control.

The CPU context matcher recognizes a complete joined packet, a head or a
tail by exact payload bytes. It changes only the observed destination-port
bits in packet word 5 and the selected TX control in word 27. Other word-5
bits and source descriptor lengths are preserved. Word 5 was `00008000`
for both whole and partial contexts; it must not be treated as the frame
length.

The final implementation repairs every matched fragment context while all
remain stopped at PC1924, then continues the entire mask in one write. It
does not introduce the earlier several-millisecond gap between releasing
the head and tail. The final test logs a common `mask=60` release.

The host counts exact whole frames and exact head/tail frames separately.
A 64-byte head is an independently captured Ethernet frame, not proof of a
successful 128-byte scatter-gather transmission. The tail's disposition was
not located; absence from this capture is not a claim about every egress.

## Results

Here “flags 3” means `e0000040` in each 64-byte Intel-style RXD.
“Flags 2/1” means the candidate SOP-only `c0000040` and EOP-only
`a0000040`, using the existing measured `sop_shift=29` layout.
These labels do not establish a complete vendor descriptor specification.

| Case / JSON filename suffix | Active LS field | RXD flags | CPU word 27 | Release method | Split wire result |
| --- | --- | --- | --- | --- | --- |
| `batch-first` | DW6 bit 0 | 3 / 3 | `08000000` | Each context separately | Exact 64-byte head only |
| `segment-flags` | DW6 bit 0 | 2 / 1 | `08000000` for control | Fragments do not reach CPU breakpoint | Two NP1 diagnostics, no split payload |
| `keep-control` | DW6 bit 0 | 3 / 3 | `08100000` | Each context separately | Exact 64-byte head only |
| `ls-word1` | DW1 bit 30 | 3 / 3 | `08000000` | Each context separately | Exact 64-byte head only |
| `ls-word1-segment-flags` | DW1 bit 30 | 2 / 1 | `08000000` for control | Fragments do not reach CPU breakpoint | Two NP1 diagnostics, no split payload |
| `together` | DW1 bit 30 | 3 / 3 | `08100000` | Both contexts together at PC1924 | Exact 64-byte head only |

Every filename is prefixed `wire-split-` and is under
`out/acceptance/sg-dma/`. Every verdict is `WIRE_SPLIT_FRAME_MISSING`;
every whole-frame count is `[1, 0]` (control, joined packet). The head-only
cases have fragment counts `[1, 0]`; the candidate SOP/EOP cases have
`[0, 0]`. Capture calibration passes with zero reported capture drops.

In the flags-3 cases, both exact ranges reach separate CPU contexts.
For the final run they are ME5/head and ME6/tail. The producer and consumer
pointers finish at 3, WOE enter/EOP and SIPC counts are 3, and the sampled
slice value is 1. This includes the one whole-frame control.

In the flags-2/1 cases, the two ranges do not reach PC1924. Queue 16 receives
two 32-byte diagnostic records with metadata `00001062`
(reason/detail `0x62/0x10`). NP2 count remains 1 for the successful control.
The first case's additional diagnostic excerpt is preserved in
`wire-split-segment-flags-traps.txt`; the DW1 case's JSON includes both records.

## Vendor configuration cross-check

The existing `np.ko` function `woeRxSetupMT7915 @ 0x5bdc` explicitly selects
LS word 1 bit 30 and enables descriptor mode 1. The alternate LS test uses
that field position, not an implementation of the complete MT7915 RXD format;
the input payload description remains the tested Intel-style format.

Because the diagnostic compensates the intercepted DMAD prefix's observed
byte shift, the DW1 LS bit is added after that software compensation.
The raw words logged for the control/head/tail are
`4000009b / 0000009b / 4000009b`. The address-carry byte is preserved.

`woeRxSetupWAV600 @ 0x5adc` configures mode 0, no separate DMAD and RXD
interception. The `woe_rx_ring_pointer_update @ 0x5638` and
`woe_rx_isr_init @ 0x5434` call sites use the Intel chip-mode setter in
pointer-update configuration. No verified frame-joining contract or SG
enable operation was recovered from those call sites, so no speculative
chip-mode change was added.

Preserving WOE bit 20 while adding native TX bit 27 passes the whole-frame
control and still emits the head separately. Thus clearing that bit is not
a sufficient explanation for the observed head-only result.

## Code and reproduction

`woe-wav600-probe.c` adds `wire_split`, exact control/joined/head/tail
context matching, batch descriptor submission and simultaneous context
release. It retains the bounded `segment_flags`, `ls_word1` and
`cpu_keep_control` comparisons. The earlier serial-release implementation
is archived only as `out/acceptance/sg-dma/woe-wire-split-serial.c`.

The final same-release-time comparison is:

```sh
python3 port/mainline/sg-acceptance/woe-wire-accept.py --module-args 'descriptor_bp=5 cpu_steps=1 cpu_metadata=3 cpu_keep_control=1 separate_rxd=1 rxd_swap=1 dmad_swap=1 dmad_align=1 sop_shift=29 unknown_sid=0 ssid_value=0x8000000f split=1 wire_split=1 ls_word1=1'
```

Build and copy the module as described in the fixture README. Each load
requires a fresh RAM boot and a final reboot, including a failed probe.
The final `wire_split` mode uses metadata-only replay, holds the contexts at
PC1924 and resumes them together; it does not run the downstream CPU trace
one instruction at a time. The mandatory `cpu_steps=1` enables that fixture
path. The old non-split CPU trace remains available.

Evidence JSON preserves controlled packets, replay/readback lines, context
classification and selected counters. `board_excerpt=true` identifies
selected logs, not a complete console transcript.

## Remaining implementation boundary

These experiments do not recover a hardware rule associating multiple
payload addresses with one Ethernet frame. Accepting both inputs as complete
packets emits at least the head independently; the candidate partial-packet
flags instead take the NP1 diagnostic exit. This is a limit of the tested
descriptor/firmware path, not proof that all silicon paths lack SG.

A production implementation still needs a verified multi-buffer descriptor
or continuation protocol, ordinary submission without per-packet debugger
intervention, and final source-buffer ownership/reuse semantics.
The sampled release counts remain 0/0/0; DMA allocations remain pinned until
reboot. Production driver code and `NETIF_F_SG` were not changed.

The next evidence search must target that multi-buffer association protocol,
rather than repeating the flags/LS combinations above or treating independent
DMA reads as SG completion.

## Validation and recovery

The maintained module passes `W=1` and strict checkpatch with zero errors,
warnings or checks over 1,731 lines. Python syntax and Git whitespace checks
pass. Final code and evidence hashes are in
`recovery-wire-split/validation.json`.

Final recovery boots the unchanged 10,997,052-byte FIT, SHA-256
`65b0fa1174e37603d770b075446b9631ca367daa39a1e3af6812ea3894678d29`.
One TFTP load needed additional time for retransmission; the complete byte
count was verified before booting. No flash or saved environment was changed.

WAN and LAN each pass eight streams and 16 MiB with zero bad bytes.
WAN queue submissions/completions are 563/563 and 11,430/11,430;
LAN is 529/529 and 11,169/11,169. Error, timeout and pending deltas are zero.

The probe is absent, WOE controls and queue-16 pending are zero, SDT104[0]
and Wi-Fi SPA word-19 fields 16..48 are clear. Breakpoint flags/status/busy
and debug mode are zero, mask is `ff`, SPR clearing is 1 and XPS masks are
zero. The kernel fault scan is empty. RX checksum remains on and SG remains
off. WAN/LAN addresses and host en8's 2.5G topology are preserved.
