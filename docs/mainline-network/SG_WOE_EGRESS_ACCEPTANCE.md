# WOE full-frame WAN egress acceptance, 2026-09-07

Status: **the 128-byte WOE wire control passes with RAM-only packet-metadata
writes. SG, a production submission path and source-buffer completion remain
unimplemented.**

Three independent RAM boots each produced exactly one expected Ethernet
frame at the WAN peer. Two runs used only the port and TX-control writes;
the last resumed ordinary execution after one additional entry instruction.
The older conclusion that the WOE complete-frame control never reaches WAN
is superseded by these results.

The subsequent [wire split tests](SG_WIRE_SPLIT_TESTS.md) reuse this control
with two noncontiguous ranges. They preserve whole-frame egress but do not
produce a joined frame. This report records the preceding whole-frame
milestone; SG remains unimplemented.

## Successful path and tested scope

The probe submits a CPU-owned 128-byte Ethernet/IPv4/UDP buffer through the
flat WAV600 descriptor input, with `rxd_swap=1 sop_shift=29 unknown_sid=0`
and temporary SDT104[0] `8000000f`. It stops the exact packet at CPU-input
PC1924 and changes these persistent packet-memory fields:

| Word | Original WOE value | Successful value |
| --- | --- | --- |
| 5 | `00008000` | `00008018` — the observed WAN-port operand 6 |
| 27 | `00100000` | `08000000` — the native TX control used by this firmware |

Writing word 31 from zero to `ffff0000` was included in the first successful
case, then omitted in two successful follow-ups. It is not required for this
complete-frame experiment. The two-field form is the smallest successful
configuration tested here, not a full decoding of either word.

The host independently constructs the expected Ethernet bytes, including
the board's current MAC address, IPv4/UDP checksums and the distinct test
payload. Every successful run has two captured ping-control replies and zero
capture drops. The successful frame is 128 bytes in the Ethernet capture;
this does not count the on-wire preamble or FCS.

| Evidence JSON | Metadata mask | Trace after the metadata writes | Exact WAN frames | Result |
| --- | --- | --- | --- | --- |
| `cpu-metadata-all.json` | 7: words 5, 27, 31 | 48 observed CPU-input PCs | 1 | `WOE_WIRE_CONTROL_PASS` |
| `cpu-metadata-port-control.json` | 3: words 5, 27 | 48 observed CPU-input PCs | 1 | `WOE_WIRE_CONTROL_PASS` |
| `cpu-metadata-final.json` | 3: words 5, 27 | PC1924, PC1925, then normal continue | 1 | `WOE_WIRE_CONTROL_PASS` |

These are one-packet tests on separate boots, not sustained traffic or a
descriptor-reuse test. No fragment experiment was run on this new egress
path in that continuation. The later `wire_split` fixture is documented in
the linked split report; ordinary `cpu_steps` still rejects `split`.

## Why the earlier scalar replay was insufficient

The earlier [CPU-input comparison](SG_CPU_EGRESS_TRACE.md) substituted SPR9
and SPR15 while leaving packet-memory words 5 and 27 unchanged. That followed
the same 48 observed PCs as native TX but produced no WAN frame.

The new `cpu-output-native.json` and `cpu-output-woe.json` capture all 32
packet-metadata words and the SPR/RSP state before PC2020. They confirm that
the stable word-5/27/31 differences survive that section. An apparent SPR1
difference was not stable: `cpu-special-native.json` also transmitted
successfully with SPR1 `00000262`, as seen in the WOE case. It was therefore
not used as a field to normalize.

The successful A/B changes packet memory itself. The hardware finishes the
normal CPU-input code and emits the expected frame. This establishes a
working whole-frame handoff for the tested input; it does not yet locate or
decode every later consumer of the persisted metadata.

## Calibrated packet-memory writes

The CPU observation window itself is not writable. The probe instead replays
existing instruction formats through the calibrated hardware step interface:

1. Set the low and high halves of SPR3.
2. Store SPR3 to the selected packet-memory word.
3. Read the word back and require the exact expected value.

Each injected operation executes at the stopped PC1924 context, followed by
a checked always-branch from PC1925 back to PC1924. The original instruction
RAM block has already been restored. The real entry instruction subsequently
reloads SPR3, so the scratch value does not replace normal packet processing.

The field-store format is calibrated against existing firmware stores,
including `040000180000029b` for SPR3 to packet word 5. The corresponding
word-27/31 forms are verified by hardware readback in the captured experiments.
Every replay verifies the stopped ME and expected next PC. Only the context
whose entire 128-byte payload matches the controlled test packet is modified.

This method writes metadata, not the packet payload. It still requires
per-packet debugger intervention and is not suitable as a production TX path.
The production driver, firmware file and FIT remain unchanged.

## Reproduction

Build and copy the standalone module as described in
[`sg-acceptance/README.md`](../../port/mainline/sg-acceptance/README.md).
Start from a fresh RAM boot and reboot again afterward, including on failure.

```sh
python3 port/mainline/sg-acceptance/woe-wire-accept.py --module-args 'descriptor_bp=5 cpu_steps=1 cpu_metadata=3 rxd_swap=1 sop_shift=29 unknown_sid=0 ssid_value=0x8000000f'
```

`cpu_metadata` is a diagnostic mask: bit 0 writes word 5, bit 1 writes word
27, bit 2 writes word 31. It cannot be combined with scalar control/port
replay or native control. The full-frame size and the original metadata are
explicitly checked. The acceptance decision is the host wire verdict;
`matched=0/0` only says that this packet was not returned to the old
queue-16/software receive fixture.

Evidence resides under `out/acceptance/sg-dma/`. CPU JSON stores all observed
PCs, changes to sampled registers, final output snapshots where available,
metadata replay/readback, controlled wire frames and capture calibration.
`board_excerpt=true` identifies selected log lines, not a complete raw log.
Missing register keys in trace rows inherit the previous row's value.

## Remaining SG gates

The prior separate-DMAD/RXD experiment demonstrated reads of two noncontiguous
64-byte ranges. It has not yet been combined with this working Ethernet egress
control. The next payload test must distinguish one 128-byte wire frame from
two separate frames, using a working whole-frame control in the same setup.
An intermediate contiguous buffer is not required.

The metadata also needs to be generated in the normal submission path,
without stopping every packet. The debug writer is evidence for the required
handoff, not a driver implementation of that handoff.

`cpu-metadata-ownership-check.txt` records source DMA address `9bc61000`,
one SMCT/NP2 transfer, correct IDM length count 1 and no queue-16 return.
The sampled release counts remain 0/0/0 and release words are unchanged.
No final ownership/reuse rule was recovered. All submitted DMA memory stays
pinned until the mandatory RAM reboot. `NETIF_F_SG` stays disabled.

## Validation and recovery

The maintained module passes `W=1` and strict checkpatch: zero errors,
warnings or checks over 1,597 lines. No production driver source changed.
The source and evidence hashes are recorded in
`recovery-woe-egress/validation.json`.

Final recovery reloaded the unchanged 10,997,052-byte FIT, SHA-256
`65b0fa1174e37603d770b075446b9631ca367daa39a1e3af6812ea3894678d29`.
One intermediate TFTP attempt exhausted its retries; another complete
transfer was verified before booting. No flash or saved environment changed.

WAN and LAN each passed eight streams and 16 MiB with zero bad bytes.
WAN submitted/completed 909/909 and 11,057/11,057 across its two TX queues;
LAN submitted/completed 435/435 and 11,262/11,262. Error, timeout and pending
deltas were zero.

The probe is absent. WOE controls, queue-16 pending, SDT104[0], all Wi-Fi SPA
word-19 entries 16..48, breakpoint flags/status/busy and debug mode are clear.
The normal breakpoint mask is `ff`, SPR clearing is 1 and all four XPS masks
are zero. The kernel fault scan is empty, RX checksum remains on and SG is
off. WAN/LAN addresses and host en8's 2.5G topology are preserved.
