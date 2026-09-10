# WOE descriptor and payload experiments

These are RAM-only diagnostic modules. Both intentionally retain DMA memory until
reboot and must not be loaded during normal router operation. They do not enable
SG on the production network device.

- `woe-rx-probe` stops SIPC RX and checks MT7915-style descriptor consumption.
- `woe-wav600-probe` leaves SIPC running and attempts one known Ethernet/IPv4/UDP
  frame through the WAV600 input format. `split=1` submits two noncontiguous
  payload ranges only if that first frame is received and verified exactly.
  `unknown_sid=0` clears the Intel unknown-station flag for a controlled comparison.
  `admit_np1=1` reproduces the ISU weight A/B on an older FIT: after submitting
  one frame it changes the fourth weight from zero to one, without resubmitting.
  The corrected production FIT already initializes this weight to one.
  `variants=1` compares four complete descriptors (SID 0, SID 128, EP 1,
  OWNER_FW) and cannot be combined with `split`, `admit_np1` or either
  instruction marker.

The factory `np.ko` contract and measured results are documented in
[`SG_DMA_CONTRACT.md`](../../../docs/mainline-network/SG_DMA_CONTRACT.md).

`descriptor_bp=5 cpu_steps=64` traces the exact complete control frame through
the CPU-input section. `cpu_control=1/2/3` and `cpu_wan_port=1` are guarded
single-packet replay diagnostics. The trace requires `wire_tx=1`, one complete
frame and a fresh RAM boot; `woe-wire-accept.py` sets `wire_tx` automatically.
It does not establish SG or DMA completion. Calibration, A/B results and
reproduction are in
[`SG_CPU_EGRESS_TRACE.md`](../../../docs/mainline-network/SG_CPU_EGRESS_TRACE.md).

`cpu_metadata=3` writes the observed WAN port and native TX control into the
exact test packet's metadata before normal processing. Combined with
`descriptor_bp=5 cpu_steps=1 rxd_swap=1 sop_shift=29 unknown_sid=0
ssid_value=0x8000000f`, it passes the complete-frame WAN control. This remains
a per-packet debugger experiment; the basic mode rejects `split` and descriptor
reuse. See
[`SG_WOE_EGRESS_ACCEPTANCE.md`](../../../docs/mainline-network/SG_WOE_EGRESS_ACCEPTANCE.md)
for independent wire evidence and the source-buffer ownership boundary.

`wire_split=1 split=1 separate_rxd=1` extends that control to one batch of two
noncontiguous ranges. It requires `descriptor_bp=5 cpu_steps=1 cpu_metadata=3`
and `repeat=1`. The fixture recognizes complete/head/tail contexts by exact
payload, repairs all matched metadata, then releases the stopped group
together at PC1924. `segment_flags=1` tests candidate partial-packet flags;
`ls_word1=1` uses the factory MT7915 LS field position; `cpu_keep_control=1`
retains WOE bit 20 while adding TX bit 27. These are bounded diagnostics,
not a working SG implementation. The same capture must pass the whole-frame
wire control before its fragment result is interpreted. See
[`SG_WIRE_SPLIT_TESTS.md`](../../../docs/mainline-network/SG_WIRE_SPLIT_TESTS.md)
for the complete matrix and mandatory RAM recovery procedure.

Build against the exact kernel used by the RAM FIT:

```sh
docker run --rm -v /Volumes/code:/Volumes/code \
  -w /Volumes/code/zx279133 sr1010-stage15-builder \
  make -C linux-6.18.38 O=/Volumes/code/zx279133/out/kernel \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 \
  M=/Volumes/code/zx279133/port/mainline/sg-acceptance modules
cp port/mainline/sg-acceptance/woe-rx-probe.ko out/woe-rx-probe.ko
cp port/mainline/sg-acceptance/woe-wav600-probe.ko out/woe-wav600-probe.ko
```

Before loading, have serial access and the known-good FIT available over TFTP.
The module requires the inactive WOE registers to match the recovered MT7915
configuration. It rejects an active engine or mismatched configuration.

On the RAM-booted board, obtain the module before stopping networking:

```sh
tftp -g -r woe-rx-probe.ko -l /tmp/woe-rx-probe.ko 192.168.1.100
insmod /tmp/woe-rx-probe.ko arm=1
dmesg | grep woe-sg
reboot -f
```

Interrupt U-Boot, then reload the FIT into RAM:

```text
setenv bootargs console=ttyAMA0,115200n8 rdinit=/init
tftpboot sr1010-zxdbg.itb
bootm
```

Wait for a complete TFTP transfer before `bootm`. Do not save the environment.
Restore the observed WAN/LAN interface configuration and verify normal traffic.
The probe has no unload path: do not try to restore SIPC and resume traffic with
its queued synthetic descriptors or infer DMA completion from disabling WOE.

For the WAV600 experiment, copy `woe-wav600-probe.ko` to the TFTP directory and
load that module instead, on a fresh RAM boot:

```sh
tftp -g -r woe-wav600-probe.ko -l /tmp/woe-wav600-probe.ko 192.168.1.100
insmod /tmp/woe-wav600-probe.ko arm=1 split=1
dmesg | grep woe-wav
reboot -f
```

The fixture uses the existing WAN peer `192.168.1.100`, MAC
`00:e0:41:68:0d:86`, and board address `192.168.1.1`. Its receive hook compares
the test frame, including IPv4/UDP checksums and nonzero payload, while allowing
the existing LAN transport VLAN to be stripped. It removes the hook after the
run. The source checks the existing three reserved IDM release-ring addresses
and requires zero outstanding releases before starting. Both SID-flag cases
still require a fresh boot; an empty ring count does not free retained DMA memory.

The WAV600 trace includes WOE/SIPC/NP1, all eight PPU engines, IDM DMA counters,
and the three reserved release rings. `wifi_trap_pending` is the packed queue
16/17 count from IDM offset `0xe4`; these queues are outside the production
Ethernet RX poll set. A trap is diagnostic evidence, not a verified frame or
permission to recycle a submitted buffer. The default complete-frame test
returns reason/detail `0x62/0x10` and a 32-byte descriptor copy on queue 16, so
the split-frame gate remains closed. See the contract document for raw captures.

The probe reads SDT0, 100, 101 and 104 definitions and their first ERAM block
before submitting data. SDT0 provides an existing known-value check for the
read transaction. These accesses use the CPU's SE command port; use a fresh,
idle RAM boot without concurrent flow-table configuration.

`trap_marker=1` is an explicitly enabled instruction-RAM diagnostic for the
documented Intel firmware. It checks all four instructions at PC5216..5219,
changes the PC5217 detail literal from `0x10` to `0x11`, and verifies the entire
block. The board returns the matching `0x62/0x11` trap. This confirms a diagnostic
detail source; it does not establish the reason for the trap or payload forwarding.
Reboot even if module loading fails. The on-disk firmware/FIT is not patched.

`branch_marker=5177` changes only the existing early branch's condition to the
candidate always condition `0x520`, preserving its target PC5222. The returned
detail changes to `0x07`, providing a working branch-marker control.
`branch_marker=5180` or `5181` instead changes the candidate target field of a
different instruction family to PC5222. Both returned the original `0x10`;
this does not validate that family's encoding or establish branch outcomes.
Each mode checks its entire four-instruction block before and after the write,
requires a fresh RAM boot, and cannot be combined with `trap_marker` or `variants`.

Example independent runs, each followed by the RAM-reboot procedure above:

```sh
insmod /tmp/woe-wav600-probe.ko arm=1 variants=1
# On another fresh boot:
insmod /tmp/woe-wav600-probe.ko arm=1 trap_marker=1
# On another fresh boot:
insmod /tmp/woe-wav600-probe.ko arm=1 branch_marker=5177
```

Experimental register sampling, temporarily disabling SPR clearing, per-thread
key reads and self-branch holding did not establish a context correlated with
the submitted frame and were removed from this fixture. Historical source and
raw captures are retained only under `out/acceptance/sg-dma/` for research.

`spa-port-read.sh` reads SPA words 0 and 19 on an idle mainline RAM boot.
`spa-wifi-attrs-ab.sh apply` is a separate RAM-only experiment that installs
the factory-captured word-19 values for ports 16–48 and verifies them. It must
be followed by the WOE probe and a fresh RAM boot. The tested copy alone did
not change the `0x62/0x10` diagnostic or verify payload/release completion.
These scripts use the SPA read/write command port and require no concurrent
table programming. On factory Linux, use the driver's locked sysfs getters
instead. See the factory-baseline section of `SG_DMA_CONTRACT.md`, including
the observed automatic firmware synchronization during full factory startup.

`path_trace=1` checks NP1 reachability using temporary jumps to the proven
detail-7 diagnostic exit. It restores and verifies each four-instruction block
before the next case. Queue-16 records are read through the exact mainline
driver's coherent descriptor mapping and are never acknowledged. It also tests
small changes to the candidate displacement fields at PC5180/5181.

`field_probe=1` compares four values at PC5181, with both outcomes redirected
to existing diagnostic exits. `field_probe=2` adds a bitwise comparison of the
candidate source operand. Neither mode permits a packet to enter the payload
path during its instruction probes. The unmodified baseline runs first.
Each mode requires an independent fresh RAM boot and cannot be combined with
`split`, `variants`, other instruction markers, or `admit_np1`.

`rxd_swap=1` compares RXD byte ordering for CPU-owned coherent memory;
`sop_shift=29` compares the flag position measured at the NP1 predicate.
Defaults remain the public Intel format (`rxd_swap=0`, `sop_shift=28`). These
are diagnostic format comparisons, not advertised netdev capabilities.

The 2026-09-06 continuation verified complete payloads on IDM queue 16, which
the ordinary Ethernet NAPI path does not poll. The fixture now compares a
queue-16 frame through the driver's DMA/page mappings as well as its packet
callback. By default it does not acknowledge those records; source buffers
remain retained in all modes.

For the reproduced complete-frame and split comparison, first RAM-reboot and
load the two files from TFTP, then run:

```sh
sh /tmp/spa-wifi-attrs-ab.sh apply
insmod /tmp/woe-wav600-probe.ko arm=1 rxd_swap=1 sop_shift=29 \
  unknown_sid=0 ssid_value=0x80000010 split=1 repeat=3
```

`ssid_value` temporarily writes inactive SDT104 entry 0, verifies the write,
and restores it before returning. `repeat` permits 1–6 complete frames before
the split gate. The observed result is `matched=3/0 bad=0`: three exact
128-byte frames, then two separate 32-byte NP1 diagnostics `0x62/0x10` for
the two noncontiguous fragments. The fragments were not gathered. Reboot the
RAM FIT afterward to clear the SPA changes and release all retained memory.

A subsequent experiment separates WOE DMAD last-section bits from the Intel
SOP/EOP predicate. On another fresh RAM boot, fetch the same module and SPA
helper, then run:

```sh
sh /tmp/spa-wifi-attrs-ab.sh apply
insmod /tmp/woe-wav600-probe.ko arm=1 separate_rxd=1 rxd_swap=1 \
  dmad_swap=1 dmad_align=1 sop_shift=29 unknown_sid=0 \
  ssid_value=0x80000010 split=1 repeat=3 ack_traps=1
dmesg | grep woe-wav
reboot -f
```

`separate_rxd=1` uses a 32-byte DMAD per input, pointing to an independent
32-byte RXD header. DMAD word 6 bit 0 carries the last-section value, while
both Intel fragment headers retain complete-frame flags. `dmad_swap` controls
DMAD word swapping independently of RXD swapping. `dmad_align` compensates
the measured one-byte displacement of the forwarded DMAD prefix and requires
`separate_rxd`. These values describe a calibrated experimental layout.

The verified result is three exact 128-byte frames and two **separate** exact
64-byte fragments: `matched=3/0 bad=0`. No assembled second frame is counted.
`ack_traps=1` consumes the five inspected queue-16 records, leaving zero pending,
but does not refill RX buffers, return pages, or release source DMA memory.
The three source-release counts remain zero. This option is incompatible with
instruction/table bit probes, descriptor variants and `admit_np1`. It does
not make the module unloadable or permit ordinary operation before reboot.
See the independent-DMAD section of the contract document for registers,
byte-shift evidence, ownership limits and final recovery results.

`key_snapshot=1` compares PPU thread keys and captures last-request registers
immediately after the first trap. `ssid_probe=1` separately compares each bit
of zero SDT104 entry 0 and restores the original block; it stops if a submitted
case no longer returns one queue-16 diagnostic. `field_probe=3`, with
`ssid_value=0x80000000`, samples selected NP1 address/length registers at
PC5198. It uses a trapped first baseline, fixed source DMA address, and two
calibrated diagnostic outcomes. It is a research probe, not a full decoder
of the microcode instruction set. The SSID-bit and register-bit probes require
independent fresh RAM boots and cannot be combined with a split/repeat run.

`descriptor_bp=1` uses the factory PPU hardware-breakpoint opcode at PC5204 to
read the actual internal NP1 descriptor. Values 2 and 3 instead stop at NP2
PC4791 and PC4729. Each site checks its full original instruction block and
restores it before continuing the actual stopped ME. An old breakpoint PC can
survive RAM reboot, so the fixture also requires the current stopped-status
bit before reading a context or continuing it. SPR, RSP and packet-memory
readback are logged; only the resumed full-frame comparison opens the split gate.

For a fresh-boot combined control, add `descriptor_bp=1` to the separate-RXD,
`split=1 repeat=3 ack_traps=1` command above. The final maintained-code run
still verifies three complete packets and two independent halves, not a
gathered second packet. This mode is incompatible with instruction/table
probes, descriptor variants, `admit_np1` and `key_snapshot`. All DMA memory
remains retained until reboot. The failed experimental `field_probe=4` mode
was removed; the established field modes remain 1–3.

See [the breakpoint evidence](../../../docs/mainline-network/SG_DESCRIPTOR_BREAKPOINTS.md)
for the vendor interfaces, exact internal words, stale-context correction,
NP2 output boundary and final RAM recovery.

The wire-boundary continuation adds `wire_tx=1` to address the packet from
the board to the fixed WAN peer. `descriptor_bp=4` stops at the final NP1
handoff, PC5216; `split_bp=1` captures each separate fragment after the complete
control. `descriptor_bp=5` observes CPU-input metadata at PC1924.
`native_control=1`, with `wire_tx=1 descriptor_bp=5`, sends one complete
reference frame through the existing netdev TX path, with WOE left disabled.
It is only a positive capture control, not hardware SG.

`woe-wire-accept.py` downloads the module, runs a bounded host capture with
ping-reply controls, independently compares the test frame, and prints JSON.
Its `--module-args` supplies the remaining module parameters, and `--wifi-attrs`
applies the established temporary SPA fixture. The script does not reboot
the board: a fresh RAM boot before each run and recovery afterward remain
required. It discards unrelated captured packets and stops its own tcpdump.

The final native control reports `NATIVE_CONTROL_PASS`. The WOE split case
reports `WOE_EGRESS_BLOCKED`, because even its full-frame control does not
reach WAN. Two intermediate queue records do not prove two Ethernet frames
on the wire. See [wire tests](../../../docs/mainline-network/SG_WIRE_BOUNDARY_TESTS.md)
for the commands, metadata comparisons and the corrected acceptance boundary.
The unsuccessful packet-window write and CPU-operand replay modes were removed.

### CPU response tracing

`poison_source=1` changes the retained source payload only after exact CPU
packet matching; `poison_source=2` changes it at the checked NP1 handoff.
Both are source-lifetime diagnostics and require a fresh RAM reboot after
submission. The [source ownership experiments](../../../docs/mainline-network/SG_SOURCE_OWNERSHIP_TESTS.md)
document the gates, commands and explicit diagnostic verdicts. In particular,
WOE descriptor RD advancement has been shown not to guarantee payload-read
completion. These options never free the source allocation.

Wire runs now keep the host UDP destination port open to avoid an ICMP
port-unreachable response. They also record paired ODMA debug reads before
submission, while the matching CPU context is held, shortly after continue,
and later. These are sequential debug reads, not atomic packet snapshots.
Use `odma-desc-decode.py CAPTURE.json` on the saved staged logs and assess
direction, lengths and counter deltas before associating a descriptor with
the test frame. See the [controlled association report](../../../docs/mainline-network/SG_ODMA_FRAME_ASSOCIATION.md).

The current `descriptor_bp=5 cpu_steps=64` diagnostic emits
`cpu-output pc=N` followed by packet/SPR/RSP snapshots at PCs 1981, 1982
and 2018..2020. `cpu-response pc=N word=W value=V` records the initial
32 RSP words and subsequent changes at each stopped instruction. Values are
debug observations; unchanged response data is not a completion event.
Keep snapshots grouped by their PC when processing the raw board log.
The full-frame native and repaired-WOE runs, their limits and final RAM
recovery are recorded in the [PPU consumer trace](../../../docs/mainline-network/SG_PPU_CONSUMER_TRACE.md).
# Investigation closed, 2026-09-10

SG/TSO/TSO6 remain blocked and disabled. These sources are retained diagnostics,
not production offload implementations. The user stopped further exploration.
See [final PPU results](../../../docs/mainline-network/TSO_PPU_CONSUMER_TRACE.md)
and [current offload status](../../../docs/mainline-network/NIC_OFFLOAD_PROGRESS.md).
`run-tso-template.py` performs a retained-source probe and mandatory RAM reboot;
`tso-trace-audit.py` verifies saved traces without touching hardware;
`tso-trace-state.py` reads the board and saved recovery results.
