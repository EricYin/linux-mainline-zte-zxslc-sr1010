# CPU-IDM template output: bounded wire acceptance

2026-09-09. Follow-up to [the static TSO audit](TSO_HARDWARE_FEASIBILITY.md).
These tests use the known flag3 descriptor fields without enabling Linux
TSO/TSO6. The physical output tested is WAN; IPv4 and IPv6 refer to packet
format, not separate network interfaces.

## Verified zero-payload behavior

| Packet format | Descriptor mode | Logical payload | Actual payload per output | TCP sequence offsets | Completion delta |
| --- | --- | ---: | --- | --- | ---: |
| IPv4 | ordinary flag2 control | 64 | 64 | 0 | 1 |
| IPv4 | template flag3 | 64 | 64 | 0 | 1 |
| IPv4 | template flag3 | 128 | 64, 64 | 0, 64 | 1 |
| IPv4 | template flag3 | 145 | 64, 64, 17 | 0, 64, 128 | 1 |
| IPv6 | ordinary flag2 control | 64 | 64 | 0 | 1 |
| IPv6 | template flag3 | 64 | 64 | 0 | 1 |
| IPv6 | template flag3 | 128 | 64, 64 | 0, 64 | 1 |
| IPv6 | template flag3 | 145 | 64, 64, 17 | 0, 64, 128 | 1 |

Each row publishes one diagnostic descriptor. MSS is 64. Template length is
118 bytes for IPv4 or 138 for IPv6, including Ethernet header but excluding
FCS, regardless of logical payload 64/128/145. Repeat and tail fields encode
the additional outputs. Every row also sends a separate same-format complete
control through the normal driver before the isolated diagnostic submission;
that control is excluded from the output counts above.

All output IP/TCP lengths, TCP sequence values and checksums match independent
reconstruction. Other bytes match the complete control, with the explicitly
expected changes. IPv4 ID remains 0x5401 on every output even with DF clear.
TCP ACK/PSH flags remain 0x18 on every output. These observed fixed-ID and
repeated-PSH behaviors are not a validation of general Linux TSO semantics.

`audit-tso-template.py out/acceptance/tso-template` passes 72 independent
checks over the eight zero-payload/control cases. This is a template behavior
acceptance, not a TSO feature acceptance. Every case has one short observation,
not a long-duration or high-rate stress test.

## Submission, observation and lifetime

`tso-template-probe.c` builds one template in a retained skb allocation and
uses the same streaming `dma_map_single` path as ordinary TX. Before the
diagnostic it disables ordinary TX, detaches the netdevices and cancels the
TX reclaim worker. It verifies that both software TX rings have no pending
work, publishes one descriptor under the driver's TX lock and observes the
hardware completion register for approximately 1.2 seconds. An empty software
owner prevents automatic unmap/free of the diagnostic source. The mapping,
skb and netdevice reference remain allocated until RAM reboot; the module
has no unload path.

The counter increases by one even when two or three frames are observed.
This establishes a bounded descriptor-count observation. The first-change
timestamp is software polling latency, with roughly millisecond sampling,
not the instant of hardware completion. Host capture and board clocks are
not synchronized. No source poisoning/reuse test was performed at the first
completion edge, so final-read timing and safe early reclamation remain
unproven. No buffer is released based on this observation.

Each case reloads the same known-good 11003260-byte RAM FIT and verifies the
TFTP transfer before booting. Source data is never reused between cases.
The FIT SHA256 is
`43bef302fe172abb1c7def61cdebd319ef60fde29131d8c614af6000a9c7893d`.
The driver and its RSS programs remain unchanged. No factory boot, flash,
NAND or saved boot-environment change is used.

## Fixture calibration and excluded attempts

The initial coherent allocation at a page boundary produced a completion
increment but no matching wire frame, while the same frame through normal
driver TX arrived correctly with matching descriptor control fields.
Changing the diagnostic to the driver's skb allocation/headroom and streaming
mapping made the ordinary direct-submit control pass. These properties were
changed together: the experiment does not isolate cache coherency from layout
or prove that coherent DMA allocations are generally unusable.

Early host capture cleanup targeted the sudo wrapper rather than its tcpdump
child. One attempt delayed recovery and emitted ordinary TX-watchdog messages
while the fixture kept queues stopped. Capture cleanup now targets the actual
child, records the probe before cleanup, and runs RAM recovery in an outer
finally block. The isolated interfaces are detached so normal TX-watchdog
recovery is not applied to an intentionally paused diagnostic. Both capture
cleanup failures and the no-wire coherent trials remain excluded records.

The maintained collector uses immediate capture mode, calibrates peer
reachability, includes a normal-driver TCP control, checks packet-socket
capture drops, and preserves the pcap plus raw descriptor/completion logs.
One zero-mode run used a collector that supplied an extra pattern=0 parameter
to the older module; the module logged that it ignored the unknown parameter.
Its emitted descriptor and complete wire bytes still match the zero-mode
contract. The collector subsequently omits this optional parameter unless
the distinct-data diagnostic is requested.

The first final LAN readiness check failed because a fresh RAM image does
not automatically load the normal switch/tag modules and LAN address setup.
No LAN payload test ran in that attempt. Final recovery restores those exact
pre-test settings before rerunning endpoint checks.

A later recovery statistics read overlapped the TCP collector on the same
serial shell and garbled command framing before TCP data was released. That
attempt was excluded. The shared `board()` helper now serializes complete
command/response transactions with a host file lock. Two simultaneous read-only
marker calls returned their respective exact responses, and the TCP recovery
was restarted with exclusive serial use. This is a collector fix, not a driver
or datapath correction.

## Distinct payload and final recovery

The next two cases place different 64-byte blocks A and B consecutively after
the TCP header. A contains the byte ramp 0x31..0x40 repeated four times; B
contains 0x71..0x80 repeated four times. Both are initialized before streaming
DMA mapping. The mapping covers H+128 bytes (182 for IPv4, 202 for IPv6),
while the descriptor retains the verified vendor-compatible H+64 template
length and the two-output count/MSS fields. The first-template TCP checksum
includes A. There is no unmapped second block in this experiment.

**Both IPv4 and IPv6 emit A,A, not A,B.** The second frame advances TCP
sequence by 64 and has a valid checksum for the repeated A bytes. This directly
distinguishes the tested mode from segmentation over the two supplied distinct
payload ranges. It is consistent with template replay and does not recover an
advancing payload DMA operation. It does not rule out another undocumented
mode or source-length encoding; those are still unproven.

The complete independent audit passes **90 checks across ten cases**, including
both ordinary controls, six zero-template cases and two distinct-data cases:

```sh
python3 port/mainline/sg-acceptance/audit-tso-template.py \
  out/acceptance/tso-template --patterns
```

An audit PASS here means the measured template behavior is verified. In
particular, passing the distinct-data observation confirms the repeated-first
result; it does **not** mean arbitrary TCP payload segmentation passed.

Raw files include `wire.pcap`, complete frame bytes and descriptor/completion
logs per case. The tested zero-template module is retained as
`zero-template-probe.ko` (SHA256
`db6da4255b5ce2eec1e243dc15468f4513305d41293e91ef4f0d458e7fbbf8cb`),
and the distinct-data module as `pattern-template-probe.ko` (SHA256
`d7e3bddf39637884b77c086d95969c312c8c23bfae648e250c6ce1678ceb6369`).
The final source only reformats the latter's log statement; the final module
build succeeds with W=1 and strict checkpatch reports no errors, warnings or
checks. Earlier Docker builds emitted sub-millisecond host/guest clock-skew
warnings; the final build completed without those warnings.

**Implementation disposition:** the vendor-compatible flag3 mode is now a
wire-proven template replay mechanism with sequence/length/checksum updates.
It is not a usable general TSO/TSO6 implementation: distinct payload advancement,
full header/flag semantics and final-read/reuse safety remain unmet. Production
TSO/TSO6 and SG stay disabled. No new guessed control bits or alternative
descriptor-length semantics are enabled by these results.

Final RAM recovery is complete. The normal switch/tag modules and LAN address
are restored; link readiness and peer ARP are established before traffic.
WAN and LAN each receive all 4096 ordered UDP packets (8192 total), with zero
bad, missing, duplicate or out-of-order packets. TCP verifies 16 streams of
8 MiB each (128 MiB), with IRQ affinity unchanged. Final maps are f0/f0,
RXHASH active/load is 1/1, IRQ masks are 1/2, all four RPS masks are zero and
the console retains CPU0 affinity. SG/TSO/TSO6 remain off. Kernel taint is zero,
only the normal switch/tag modules are loaded, RX/TX errors, TX pending,
timeouts and refill deficit are zero, and the fault scan and host capture
process scan are empty. See `out/acceptance/tso-template/final-state.json`.

Changes are confined to removable probe/collector/audit code, the serial
collector lock and evidence/documentation. The production driver, firmware
and FIT are unchanged. No persistent board storage or host network address,
route or adapter configuration was modified, and no Git commit was made.
