# SG descriptor capture with factory PPU breakpoints

Status: **SG remains unimplemented**. This continuation adds a calibrated way
to inspect the actual NP1/NP2 state without substituting conditional branches
for field reads. It recovers the single-buffer NP1 handoff and the observed
NP2 exception exit. No multi-buffer gather or source-release contract was found,
and production driver code, firmware files and the RAM FIT were not changed.

The later [wire-boundary tests](SG_WIRE_BOUNDARY_TESTS.md) clarify that a
single intermediate record is not required for SG. The subsequent
[packet-metadata write experiment](SG_WOE_EGRESS_ACCEPTANCE.md) passes the
WOE complete-frame WAN control. The peer experiment has not yet decided
whether downstream hardware preserves one frame across fragments.

## Breakpoint implementation and calibration

The following evidence comes from the existing `np.ko` IDA database:

| Function or symbol | Recovered interface |
| --- | --- |
| `np_ppu_test_break_point_set_store @ 0xbde24` | Replace one instruction in a four-instruction block with the factory TRAP0 opcode |
| `uTRAP0_INST_CODE_L @ 0x1a0008` | Low word `17d80000`; the setter uses high word `00800000` |
| `ppu_debug_set_bp_real_inst @ 0x78d48` | Original instruction low/high words at `180900cc/d0` |
| `ppu_debug_set_me_continue @ 0x78bb0` | Continue the selected ME bitmask through `180900c0` |
| `ppu_debug_get_bp_addr @ 0x78e2c` | Per-ME breakpoint PC at `18090100 + 4*ME` |
| `ppu_debug_get_me_int_stat @ 0x78f24` | Current stopped-ME status at `18090004` |
| `ppu_get_spr_reg_value @ 0x794a8` | 32 SPR words at `18094500 + 400*ME` |
| `ppu_get_rsp_reg_value @ 0x7958c` | 32 RSP words at `18094580 + 400*ME` |
| `ppu_get_pkt_mem @ 0x793dc` | 64 packet-memory words at `18094400 + 400*ME` |

The maintained `descriptor_bp` option has five fixed, checked sites:
1 = PC5204, after NP1 writes the internal descriptor;
2 = PC4791, the observed NP2 detail-7 exit;
3 = PC4729, before its first early conditional branch;
4 = PC5216, the final NP1 handoff;
5 = PC1924, the CPU-input metadata entry.
The fixture verifies the entire original instruction block, installs the
factory opcode, submits one complete frame, reads the stopped state, restores
and verifies the original block, and resumes with the saved real instruction.
It then requires the ordinary full-frame byte comparison before a split test.

The later `cpu_steps` option calibrates hardware single stepping at site 5,
matches the exact control payload and refreshes the replay latch at every
stop. Its control/port A/B and remaining egress boundary are documented in
[SG_CPU_EGRESS_TRACE.md](SG_CPU_EGRESS_TRACE.md).

Per-ME breakpoint-PC readback can retain an old value across `reboot -f` and
RAM reload. In the first SSID-bit-29 run, ME6 still reported PC5204 and its
old `80000010` result, while the current stopped-ME status was `80` for ME7.
Only ME7's `a0000010` sample belongs to that run. The maintained fixture now
requires **both current stopped status and matching PC**, and continues only
that mask. The final run shows `status=80`, reads only ME7, and resumes `80`.
Old PC or SPR readback alone is not evidence of the current packet.

## Actual NP1 descriptor

At PC5204, the final separate-DMAD/RXD experiment captured these packet-memory
words, also agreeing with SPR16 through SPR23:

| Packet-memory word | Value | Supported interpretation |
| --- | --- | --- |
| 32 | `00000001` | Observed fixed word; not decoded as a gather flag |
| 33 | `40000000` | Agrees with logical port 16 in the high six bits |
| 34 | `9bc61000` | Exact submitted payload DMA address |
| 35 | `00020000` | Exact 128-byte length shifted left by ten |
| 36 | `00100000` | Observed control; complete semantics still missing |
| 37–39 | Zero | No additional address or segment list in these words |

SPR11 is `e0000080`, the known input control word. RSP12 is `80000010`, the
read-back SDT104 entry. A controlled comparison with `a0000010` changes
SPR20 from `00100000` to `01100000`: SDT104 bit 29 reaches internal control
bit 24. This proves a copy relation, not the field's complete meaning or a
DMA ownership flag. Both cases still deliver one exact complete frame with
zero source-release counts.

Earlier PC5198 bit sampling returned SPR20 zero. It occurs before all final
descriptor writes have taken effect and used a different SSID value; it must
not replace this stopped, serialized-descriptor observation. An experimental
late bit-sampling mode could not preserve its diagnostic outcomes and was
removed. Its failed captures remain separate from the working breakpoint data.

## NP2 and output boundary

The second breakpoint observes PC4791, where the microcode sets detail 7,
followed by reason `65` at PC4797. The two early branches at PCs4729/4730
contain that target. At the PC4791 stop, the packet-memory payload contains
the complete expected Ethernet/IPv4/UDP frame. At PC4729 the frame length
is already 128 and the logical source port is 16. Resuming either breakpoint
still returns the exact 128-byte frame on queue 16 with metadata `00000765`.
The flag and lookup semantics that select this exit have not been decoded.

A separate source-port-15 comparison uses SDT104 `8000000f` without copying
Wi-Fi SPA attributes. It increments NP2 but produces neither a verified
packet callback nor a queue-16 frame. A host WAN capture observed no matching
UDP frame. This comparison does not establish a normal Ethernet TX path or
provide a release event. Its split gate remains closed.

## Final test and recovery

The cleaned fixture combines `descriptor_bp=1`, the proven separate-DMAD/RXD
configuration, `split=1 repeat=3 ack_traps=1`. It verifies three complete
128-byte frames and both independent 64-byte halves. Queue 16 acknowledges
five records and drains to zero, while all three source-release counts remain
zero. **`matched=3/0 bad=0`** still means that no assembled second frame arrived.
The breakpoint instrumentation does not change that SG result.

The final `W=1` module build and strict checkpatch passed: zero errors,
warnings or checks, 1,372 source lines. After reloading the unchanged
10,997,052-byte RAM FIT, WAN and LAN each passed eight streams / 16 MiB with
zero bad bytes. Both TX queues completed every submission: WAN 979/11,022;
LAN 345/11,337. The fixture does not claim a throughput improvement.

Final readback confirms WOE controls, SDT104[0], queue-16 pending and all 33
temporary SPA word-19 values are zero. Breakpoint flags, status and busy are
zero; the interrupt mask is the normal `ff`, debug mode is zero, and SPR
clearing is 1. All four XPS masks are zero; the probe is absent. RX/TX errors,
TX timeouts, pending TX and RX refill deficit are zero; the kernel fault scan
is empty. RX checksum is on and SG remains off. The last TFTP transfer needed
longer than the initial 30-second observation window; completion of all bytes
was verified before `bootm`. No factory Linux, flash write or saved environment
change occurred in this continuation.

Raw evidence is under `out/acceptance/sg-dma/`:

- `np1-descriptor-breakpoint.txt`, `np2-exit-breakpoint.txt` and
  `np2-predicate-breakpoint.txt`: independently resumed breakpoint controls.
- `np1-ssid-bit29-breakpoint.txt`: SSID copy relation; disregard stale ME6.
- `np1-breakpoint-final-split.txt`: maintained code with current-status masking,
  complete-frame controls, split comparison and ACK.
- `serialized-np1-fields.txt`, `all-np1-fields.txt` and
  `serialized-np1-fields-enabled.txt`: superseded bit-sampling experiments.
- `woe-cpu-port15.txt` and `woe-cpu-port15-wire.txt`: bounded source-port comparison.
- `recovery-breakpoints/`: WAN/LAN board and peer logs, JSON results, final state
  and validation manifest.

The next implementation gate is still a supported internal multi-buffer
descriptor or sequence, with a final source-payload completion event and a
verified Ethernet egress. The newly recovered single-buffer descriptor is
not sufficient to enable `NETIF_F_SG` or recycle mapped skb fragments.
