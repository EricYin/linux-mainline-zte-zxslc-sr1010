# SG wire-boundary tests, 2026-09-07

Latest status: **WOE complete-frame egress passes with diagnostic metadata
writes; the tested split-descriptor formats do not produce a joined frame**.
See [SG_WIRE_SPLIT_TESTS.md](SG_WIRE_SPLIT_TESTS.md) for six controlled cases.
The experiments below record the earlier egress blocker before the
[whole-frame correction](SG_WOE_EGRESS_ACCEPTANCE.md).
This continuation follows the user's observation that hardware may read and
send fragments sequentially without first copying them into one contiguous
buffer. That is a valid SG architecture. The acceptance target is one correct
Ethernet frame at the peer, with recoverable source-buffer ownership. A single
128-byte intermediate queue record is not a necessary implementation step.

The subsequent [CPU-input trace](SG_CPU_EGRESS_TRACE.md) establishes causal control
and port-dependent branch differences. Replaying both follows the same
observed CPU-input PCs as native TX without producing a WAN frame. That
scalar-only result was superseded by the packet-memory writes linked above;
the subsequent split matrix is linked above. SG remains unimplemented.

## Wire observation and positive controls

`woe-wire-accept.py` runs a bounded capture on the fixed host interface en8.
It checks an independently constructed expected frame and preserves only
controlled test packets, ping-control results and the board transcript. Other
captured traffic stays in memory and is discarded. A reader thread drains
tcpdump while serial commands run, so a full stdout pipe cannot hide the test.
The script stops and joins its capture process on completion or exception.

`wire_tx=1` addresses the module's test frame from the board MAC / `192.168.1.1`
to the existing peer MAC `00:e0:41:68:0d:86` / `192.168.1.100`.
Earlier input experiments used the opposite addresses. Both forms have a
known 128-byte Ethernet/IPv4/UDP body, ports 49029/49030 and seeded payload.

Before and after submission, a ping with the `SGWIRECAL` pattern supplies a
board-to-host capture control. The capture is valid only when both replies
are observed and the final ping succeeds. This checks the actual receive
direction used to observe an Ethernet transmission from the board.

The additional `native_control=1 descriptor_bp=5` control sends the same
complete test frame through the existing netdev TX driver, stopping and
resuming it at the normal CPU-input microcode PC1924. The peer received
**one exact 128-byte frame**, and both ping controls passed. This validates
the frame construction, wire comparison, normal TX path and breakpoint
resume. It is a contiguous control packet, not an SG implementation.

## WOE output results

| Experiment | Exact test frames on WAN | Capture control | Interpretation |
| --- | --- | --- | --- |
| Ordinary netdev TX, final maintained code | One complete 128-byte frame | Two replies | `NATIVE_CONTROL_PASS` |
| WOE, corrected wire addresses, SDT104 `8000000f` | None | Two replies | Complete WOE egress is not established |
| Same WOE case, CPU-input breakpoint | None | Two replies | Complete payload reaches the CPU-input microcode but is not observed on WAN |
| WOE separate DMAD/RXD, SDT104 `80000010`, complete frame then two 64-byte fragments | None | Two replies | `WOE_EGRESS_BLOCKED`; does not decide MAC streaming SG |

In the final split case, the board independently verifies its queue-16
128-byte control and both 64-byte halves. The WAN peer sees none of these
test frames. Therefore the absence of a 128-byte split result on the wire
cannot be attributed specifically to a failure to preserve frame boundaries:
the WOE full-frame control itself does not reach this egress.

The script distinguishes a missing complete WOE wire control from a split
failure. It also reports a split that was never submitted separately from a
submitted split with no complete wire result. Even observing a correct split
frame would still leave DMA completion and buffer reuse to validate.

## Final NP1 handoff, including each fragment

`descriptor_bp=4` stops at PC5216, after NP1 has written its outgoing header
and internal descriptor. `split_bp=1` captures the first and final fragments
individually, after the complete queue-16 control passed. Each stopped ME is
selected by both its current status bit and the expected PC. Every modified
instruction block is restored and verified before continuation.

The final capture has these packet-memory words 32–39:

| Input | Internal descriptor words |
| --- | --- |
| Complete 128 bytes | `00000001 40000000 9bc61000 00020000 00100000 00000000 00000000 00000000` |
| First 64 bytes, DMAD LS=0 | `00000001 40000000 9bc62000 00010000 00100000 00000000 00000000 00000000` |
| Final 64 bytes, DMAD LS=1 | `00000001 40000000 9bc63000 00010000 00100000 00000000 00000000 00000000` |

The two fragment controls are identical here; their verified DMA addresses
differ. These eight words do not expose a recovered first/last distinction.
Other packet-header words contain differing packet identifiers, and the
complete set of sideband semantics is still unknown. This observation is
not proof that every downstream interface lacks framing information.

The final board result is `matched=1/0 bad=0`, with both separate fragment
comparisons passing. All three source-release counts remain zero.

## CPU-input metadata comparison

The existing firmware's port-flow record sends logical port 15 to PC0, whose
first call enters PC1916; the breakpoint at PC1924 observes that input.
The regular netdev descriptor supplies destination port 6 and CPU transmit
control through the existing driver. The WOE route differs at this point:

| Packet-memory field at PC1924 | Native control | WOE, source port 15 |
| --- | --- | --- |
| Word 5, including destination-port operand | `00008018` | `00008000` |
| Word 27, transmit control | `08000000` | `00100000` |
| Word 31 | `ffff0000` | `00000000` |

The native port operand is 6; the WOE operand is zero. The payload itself is
intact in both stopped contexts. These are concrete metadata differences,
not a complete explanation of all downstream decisions.

A bounded attempt to write the stopped packet-memory observation window
left word 5 unchanged on readback. Further diagnostic replay changed the
CPU-control register to `08000000`, verified at PC1928, and substituted the
WAN-port operand. No matching wire frame resulted. This did not establish
a conversion of all WOE metadata into native CPU TX metadata. The unsuccessful
window-write/replay options were removed; their source snapshot and results
are retained only as experimental evidence.

## Reproduction and evidence

Each module load requires a separate fresh RAM boot and a final RAM reboot.
The module intentionally retains DMA buffers; do not unload it or resume
ordinary operation in the diagnostic boot.

```sh
python3 port/mainline/sg-acceptance/woe-wire-accept.py \
  --module-args 'descriptor_bp=5 native_control=1'

# After another fresh RAM boot:
python3 port/mainline/sg-acceptance/woe-wire-accept.py --wifi-attrs \
  --module-args 'separate_rxd=1 rxd_swap=1 dmad_swap=1 dmad_align=1 sop_shift=29 unknown_sid=0 ssid_value=0x80000010 descriptor_bp=4 split_bp=1 split=1'
```

Raw evidence in `out/acceptance/sg-dma/` includes `wire-native-final.json`,
`wire-full-port15.json`, `wire-woe-cpu-input.json`, `wire-split-final.json` and
`np1-handoff-split.txt`. The unsuccessful metadata tests are
`wire-port-field-readback.json`, `wire-port-operand-replay.json`,
`wire-cpu-metadata-replay.json` and `woe-cpu-replay-experiment.c`.

The maintained module passed `W=1` and strict checkpatch, with zero errors,
warnings or checks across 1,423 lines. Python syntax checks passed. Final
recovery reloaded the unchanged 10,997,052-byte RAM FIT. WAN and LAN each
passed eight streams / 16 MiB with zero bad bytes. Both TX queues completed
all submissions: WAN 838/11,148; LAN 481/11,282.

`recovery-wire-boundaries/` contains recovery JSON and final-state readback.
WOE controls, queue-16 pending, SDT104[0], all 33 temporary Wi-Fi SPA fields,
breakpoint status and busy are clear. The normal breakpoint mask is `ff`,
debug mode is zero, SPR clearing is 1, all four XPS masks are zero and the
probe is absent. RX/TX errors, TX timeouts, pending TX and refill deficit are
zero; the kernel fault scan is empty. RX checksum is on and SG remains off.
Host en8 remains `192.168.1.100`, with a 2.5G link, no bridge membership and
no QEMU process. No production driver, firmware file, FIT, saved U-Boot
environment or flash contents were changed in this continuation.
