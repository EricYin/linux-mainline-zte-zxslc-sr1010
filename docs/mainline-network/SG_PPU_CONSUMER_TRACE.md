# PPU consumer trace: observed branches, pointer reads, completion boundary

Follow-up: [controlled ODMA frame association](SG_ODMA_FRAME_ASSOCIATION.md)
locates two WOE processing stages and clarifies that packet word 9 is an
opaque pointer word, not a verified external physical DMA address.

This follow-up joins the existing CPU-input single-step captures to the
unchanged Intel firmware. It adds a reproducible annotation tool; it does
not claim a complete ISA decoder or a newly successful SG test.

Firmware SHA256:
`056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40`.
Instruction PC addresses index big-endian 64-bit words at `16 + 8 * PC`.
All observations below are from previously saved RAM experiments, not a new
board run. The native/WOE source paths and metadata replay are described in
[CPU egress tracing](SG_CPU_EGRESS_TRACE.md) and
[the successful persistent metadata repair](SG_WOE_EGRESS_ACCEPTANCE.md).

## Actual control and address observations

| PC | Firmware instruction | Evidence and interpretation |
| --- | --- | --- |
| 1924 | `0700c00018280523` | Reads packet word 5 into SPR3; its value is visible at the next stop, PC1925. |
| 1926 | `0702400018d80523` | Reads packet word 27 into SPR9; the result is visible at PC1927. Runs replacing this instruction with immediate replay must be excluded from load validation. |
| 1940 | `0290844e80028441` | Measured successor is 4244 for control 0 / `00100000`, and 1941 for native `08000000` / repaired control. This is a measured branch dependency, not a fully decoded predicate or enum. |
| 1941 | `010803cb03d1df40` | Native measured successor is 1955. |
| 1955 | `010803d203dcdf60` | Native measured successor is 1977. |
| 1977 | `0700c00018480523` | Packet word 9 to SPR3 address-value read. In `cpu-output-native.json`, PC1978 has SPR3=`a4c00000`; final packet word 9 is also `a4c00000`. The WOE counterpart has `a3ac0000` in both places. The endpoint comparison is consistency evidence, not a simultaneous source snapshot. |
| 1982 | `0703400018f80523` | Packet word 31 to SPR13; native result `ffff0000` appears at PC1983. |
| 2016 | `040000180000011b` | Matches the separately calibrated packet-word-store format: SPR3 to packet word 2. |
| 2017 | `040000280000059b` | Same format: SPR5 to packet word 11. |
| 2018 | `040000f80000041f` | Different low opcode; not assigned the preceding store semantics by this audit. |
| 2019 | `01100000088d3180` | Observed before PC2020; operation semantics remain undecoded here. |
| 2020 | `0018000000000000` | Last stopped PC in the complete native trace. The next log reports step 48 with stopped/busy masks zero. This does not identify wire EOP or source release. |

The unmodified WOE control path instead visits 4244..4249, then
4286..4299. These are observed successors; no claim is made that a static
disassembler decoded the long branch target or all instructions in that path.

**Reading packet word 9 reads an address value, not the bytes at that DMA
address.** None of the local packet-word loads above establishes the external
payload DMA instruction. Likewise, no observed instruction here has yet
been identified as loading a second source address and appending its bytes
to the currently transmitting frame. Absence of such a decoded instruction
in this bounded trace is not proof that the silicon has no continuation path.

## Reproducible trace annotation

`port/mainline/sg-acceptance/ppu-trace-audit.py` rejects a different firmware
hash, reconstructs compressed sampled-register states, and joins each
stopped PC to its firmware instruction and observed successor. It annotates
only the narrow candidate local-load encoding calibrated above:

```text
base instruction = 0700000018000523
destination SPR field = bits 50:46
packet word field = bits 25:19
```

This is an inferred encoding for this instruction form. It is not applied
to other low opcodes or described as a general load/store decoder. Each
candidate is marked `match`, `mismatch`, or `not_sampled`; missing samples
are not promoted to validation. Explicit control/port instruction replays
are marked so the firmware word is not mistaken for the executed replay.

Example, from the repository root:

```sh
python3 port/mainline/sg-acceptance/ppu-trace-audit.py out/mcode_intel.bin \
  out/acceptance/sg-dma/cpu-output-native.json \
  out/acceptance/sg-dma/cpu-path-woe.json
```

Validation over the 14 `cpu-*.json` captures containing CPU steps found
34 matching source/result comparisons, zero mismatches and 57 unsampled
candidate reads. Across those captures PC1940 had eleven observed successors
at 1941 and two at 4244; one short capture did not reach PC1940. Eleven traces
last stopped at 2020, two at 4299 and one at 1925. These counts include related
replay experiments and are not fourteen independent SG acceptance passes.

## Completion consumer, and the missing producer

The vendor `idm_get_tx_done` (`plat_132`, `0x13864`) reads the low 16 bits
of the IDM completion register: offset `0x84` for queue 0, otherwise
`4 * (queue + 42)`. The current driver's `zx279133_idm_tx_done_reg` agrees.

`net_check_tx_done_nolock` reads that value through the IDM ops callback,
computes its delta modulo 65536, and advances one software owner-ring slot
per completion. Tagged nbuf owners go to `cpu_net_free_nbuf`; other owners
follow the queue's skb recycling/freeing path. It clears each slot and
subtracts the delta from pending. The producer described in
[the IDM audit](SG_IDM_PRODUCER_AUDIT.md) records one owner per descriptor.

This recovers **how CPU software consumes ordinary IDM completions**. It
does not recover the hardware event that increments the counter, prove that
a WOE source uses this ownership domain, or explain how several SG mappings
would share one completion. Prior WOE split tests still had no demonstrated
source release. A PPU context ending, a queue-16 record, and an IDM completion
must remain separate observations until a causal link is measured.

## Remaining implementation boundary

The control-dependent branch and local address-value read are located.
External payload DMA, a continuation address/length consumer, wire-frame end,
and the source-release producer remain unresolved. The instructions around
1981 and 2018..2020 require further decoding or a targeted downstream trace;
their opcodes must not be named from the desired outcome. A future probe
must observe the downstream operation and its completion evidence, not merely
increase the CPU-entry step count after the selected context has ended.

This turn changed the annotation tool and documentation only. It did not
change the production driver, load a module, write board registers, or rerun
network acceptance. SG remains disabled.

## Subsequent RAM experiment: full snapshots and response deltas

The subsequent continuation changed the diagnostic probe and ran three
complete-frame experiments. The preceding static-only statement applies to
the earlier audit, not these runs. The probe now emits full packet/SPR/RSP
snapshots before PCs 1981, 1982, 2018, 2019 and 2020, plus an initial RSP
snapshot and changed RSP words at every stopped instruction. Instruction
replay behavior was not changed. These are debug-interface observations;
asynchronous completion and previously held response values remain possible.

| Evidence file under `out/acceptance/sg-dma/` | Outcome |
| --- | --- |
| `cpu-io-native.json` | Exact 128-byte native WAN frame; all five full snapshots present. No visible packet/SPR/RSP change across 1981→1982, 2018→2019, or 2019→2020. RSP changes occur somewhere in 1982→2018. |
| `cpu-response-native.json` | Fresh RAM boot, exact native frame. RSP4 starts `c0000000`, RSP7 starts 0; both changes are first observed at PC2000, after execution of PC1999: RSP4=0, RSP7=`00000040`. |
| `cpu-response-woe.json` | Fresh RAM boot, known word-5/27 persistent metadata repair, exact WOE whole frame. Initial RSP4=0 and RSP7=`00000040`; no RSP word changes through the 48-stop CPU trace. WOE source release counters remain `0/0/0`. |

All three captures passed their packet-capture calibration and observed
exactly one complete control frame. None submitted split payloads. The
modified probe built with `W=1`; strict checkpatch passed with zero errors,
warnings or checks (1741 lines in the final probe).

The newly localized operation is **PC1999, instruction
`0018061800620533`**. PC1997 has copied the output-port value 6 into SPR3
before this operation. That dataflow makes a port-related lookup a candidate,
but neither the destination engine nor command encoding is established.
It must not be called a payload DMA read from the RSP changes alone.

The WOE comparison is particularly restrictive: the same final RSP values
are already present before PC1999, and observing no change cannot prove
whether the operation executed, returned identical data, or retained earlier
data. The successful wire result also does not establish WOE buffer release.
No continuation-address consumer, wire EOP operation, or source-release
producer was decoded by these runs.

The next useful experiment needs an independently identified request or
completion indicator for PC1999's target engine, or a downstream descriptor
observation tied to the known control frame. Repeating identical RSP snapshots
or interpreting PC2020 as release would not close the contract.

### Recovery and acceptance

Each submitted probe's DMA allocation was kept pinned until a fresh RAM FIT
boot. The final original FIT load verified 10,997,052 transferred bytes.
Final recovery evidence is under
`out/acceptance/sg-dma/recovery-cpu-response/`:

- WAN: 8 flows, 16 MiB, zero bad bytes; queue submitted/completed counts
  909/909 and 11078/11078.
- LAN: 8 flows, 16 MiB, zero bad bytes; counts 216/216 and 11520/11520.
- TX/RX errors, pending TX, timeouts and RX refill deficit are zero.
- Probe absent; breakpoint status/flags/busy zero, interrupt mask `ff`, debug
  mode off; WOE controls and queue-16 pending zero. Wi-Fi SPA word 19 for
  ports 16..48 is zero, and SDT104 entry 0 is cleared.
- Host en8 remains `192.168.1.100/24`, 2.5 Gbit/s, outside a bridge; no QEMU
  process. Board WAN/LAN addresses remain `192.168.1.1` / `192.168.5.1`.
- Fault scan empty, console restored to 7, RXCSUM on and SG off/fixed.

No flash, NAND, saved boot environment or host address was changed.
