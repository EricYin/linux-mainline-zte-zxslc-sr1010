# WOE CPU-input path trace, 2026-09-07

Status of this earlier experiment: **first CPU-input branch divergence
localized and causally tested; scalar-only replay did not reach WAN.**

Update: [persistent packet-metadata writes](SG_WOE_EGRESS_ACCEPTANCE.md)
subsequently passed the WOE complete-frame wire control on three RAM boots.
The results below document the preceding scalar-only experiment. SG remains
unimplemented.

The bounded target was either a complete WOE frame observed on WAN or a
reproducible, causally tested divergence from native TX. This continuation
achieved the latter. It did not enable any production offload feature.

## Trace calibration and scope

The vendor `np.ko` interface `ppu_debug_set_me_step @ 0x78c7c` writes the
ME mask to `180900c4`. A calibrated step from PC1924 stops before PC1925
and changes SPR3 to the expected packet-memory word 5. The hardware leaves
the replay instruction at `180900cc/d0` unchanged. Continuing with that
stale instruction failed the native wire control; loading the instruction
at the newly stopped PC restored exact 128-byte native transmission.

The maintained probe restores the original PC1924 instruction block before
stepping, identifies exactly one stopped ME by comparing all 128 payload
bytes, resumes other stopped MEs, and refreshes the replay latch at every
stop. It traces at most 128 instructions. Every experiment starts from a
fresh RAM FIT and ends with another RAM boot; DMA buffers remain pinned.

Final native and WOE replay runs each observed 48 PCs from PC1924 through
PC2020 before stopped/busy status cleared. This is the observed CPU-input
section, not a trace of every subsequent hardware stage or a completion
notification for the source DMA buffers.

## Controlled comparisons

All wire captures passed both ping controls and reported zero capture drops.
Each run used the same frame construction and payload, with the board's
current MAC address after the fresh boot. Expected wire bytes were computed
independently on the host.

| Case | SPR9 after PC1926 | SPR15 after PC1928 | Successor of PC1940 | Successor of PC2004 | Exact WAN frames |
| --- | --- | --- | --- | --- | --- |
| Native TX | `08000000` | 6 | PC1941 | PC2009 | 1 |
| WOE unchanged | `00100000` | 0 | PC4244 | Not reached | 0 |
| WOE, clear control bit 20 | `00000000` | 0 | PC4244 | Not reached | 0 |
| WOE, native control replay | `08000000` | 0 | PC1941 | PC2005 | 0 |
| WOE, add bit 27 and replay WAN port | `08100000` | 6 | PC1941 | PC2009 | 0 |
| WOE, native control and WAN port replay | `08000000` | 6 | PC1941 | PC2009 | 0 |

The zero-control versus native-control pair changes only SPR9 bit 27,
with port zero in both cases. It changes the PC1940 branch outcome.
The two native-control WOE cases change only the replayed port operand;
port 6 removes the PC2005..2007 detour and its SPR20 bit-2 change.

The unmodified WOE path visits PC4244, constructs the value `1980002c`,
and follows the common PC4286..4299 exit. Packet-memory word 5 changes
from `00008000` to `00008020` there. These observations identify the
early exit; the value's full vendor error-name mapping is not recovered.

Adding bit 27 while retaining WOE bit 20 reaches the native PC sequence,
but PC2012 also changes SPR3 from `04400001` to `04400801`.
Removing bit 20 eliminates this difference without establishing wire egress.

## What remains unresolved

The final replay case and native control have the same 48 observed PCs,
but only native TX reaches the peer. Scalar replay does not rewrite all
persistent packet metadata. At the last observed stop, the WOE packet's
words 5, 27 and 31 remain `00008000 / 00100000 / 00000000`;
native values remain `00008018 / 08000000 / ffff0000`.
SPR13 also differs after the PC1982 load of word 31.

The next useful boundary is the actual submission after this CPU-input
section: identify the next consumer and the persisted metadata or ownership
fields it uses. Repeating scalar control/port replay alone is insufficient.
No conclusion about downstream streaming of fragments can be drawn until
the complete WOE wire control passes. Final DMA release is still unproven.

## Reproduction

Build and copy the standalone module as described in
[`sg-acceptance/README.md`](../../port/mainline/sg-acceptance/README.md).
The normal FIT and firmware files remain unchanged.

```sh
# Fresh RAM boot for each command; reboot again afterward.
python3 port/mainline/sg-acceptance/woe-wire-accept.py \
  --module-args 'descriptor_bp=5 cpu_steps=64 native_control=1'

python3 port/mainline/sg-acceptance/woe-wire-accept.py \
  --module-args 'descriptor_bp=5 cpu_steps=64 rxd_swap=1 sop_shift=29 unknown_sid=0 ssid_value=0x8000000f'

python3 port/mainline/sg-acceptance/woe-wire-accept.py \
  --module-args 'descriptor_bp=5 cpu_steps=64 cpu_control=2 cpu_wan_port=1 rxd_swap=1 sop_shift=29 unknown_sid=0 ssid_value=0x8000000f'
```

`cpu_control=1` replays zero, `2` replays native `08000000`, and
`3` replays `08100000`. These options alter the exact test packet's
replay instruction, with guarded original opcodes; they are diagnostics.

Evidence under `out/acceptance/sg-dma/`:

- `step-calibration-old-replay.json` and `step-calibration-corrected.json`.
- `cpu-path-native.json`, `cpu-path-woe.json`,
  `cpu-path-clear-control.json`, `cpu-path-native-control-replay.json`.
- `cpu-path-tx-bit-wan-port.json`, `cpu-path-native-control-wan.json`.
- `cpu-path-native-final.json` and `cpu-path-woe-final.json` repeat the
  final controls with the maintained module; both record clean probe exit.
- `recovery-cpu-path/` records final recovery and verification.

CPU evidence JSON retains every observed PC plus changes to the sampled
registers and packet fields. Missing field keys inherit the preceding row's
value. `board_excerpt=true` denotes the selected board-log lines; these
files do not claim to preserve every raw log line. Instructions are in the
unchanged firmware at byte offset `16 + 8 * PC`, stored big-endian.

## Validation and recovery

The final module passes `W=1` and strict checkpatch: zero errors, warnings
or checks over 1,510 lines. Native stepping was calibrated on the wire and
the final native/replayed-WOE pair repeated the same observed PC sequence.

Final recovery uses the unchanged 10,997,052-byte RAM FIT, SHA-256
`65b0fa1174e37603d770b075446b9631ca367daa39a1e3af6812ea3894678d29`.
The firmware SHA-256 is
`056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40`.
One TFTP transfer required additional time for retransmission; full byte
count was verified before booting.

WAN and LAN each passed eight streams and 16 MiB with zero bad bytes.
WAN queue submissions/completions were 951/951 and 10,988/10,988;
LAN was 461/461 and 11,233/11,233. Error, timeout and pending deltas were zero.

WOE controls, queue-16 pending, SDT104[0], Wi-Fi SPA fields 16..48,
breakpoint flags/status/busy and debug mode are clear. The normal breakpoint
mask is `ff`, SPR clearing is 1, XPS masks are zero, and the probe is absent.
The kernel fault scan is empty. RX checksum remains on and SG remains off.
WAN/LAN addresses and host en8 topology are preserved. No production driver,
firmware/FIT, flash or saved U-Boot environment was changed in this continuation.
