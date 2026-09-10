# WOE source lifetime: descriptor read pointer is not payload completion

## Verified result

**WOE RX descriptor RD advancement is not a sufficient condition for
reclaiming or reusing the source payload buffer.** Two independent RAM runs
advanced RD to 1, halted the matching NP1 descriptor before IDM handoff,
changed 86 source-payload bytes, and subsequently received all 86 changed
bytes through queue 16. This is a counterexample, not merely an absence of
release-counter activity.

Two other runs changed the same source bytes after the exact complete packet
was held at CPU input. The CPU packet remained original and WAN received the
original 128-byte frame. This bounds observed payload dependence for these
packets; it does not prove absence of every later bus access or authorize DMA
unmapping/freeing. All source allocations stayed pinned until RAM reboot.

## Controlled experiments

The source frame occupies `0x9bc61000` in the probe's coherent allocation.
Only bytes 42..127 are XORed with `0xff`, preserving the Ethernet/IP/UDP
headers. The helper checks the original source, performs the stores, executes
`dma_wmb()`, and checks all 86 modified bytes. The source is not replaced by
another allocation or freed.

| Point | Gate before mutation | Observation after continue |
| --- | --- | --- |
| NP1 handoff, `poison_source=2` | Exactly one stopped context at PC5216; descriptor word 34 equals the source DMA address; word 35 is `00020000`; producer is 1; logged WOE RD is 1 | Queue16 record length 128, metadata `00000765`; first 42 bytes original, original payload bytes 0, inverted payload bytes 86, other payload bytes 0 |
| CPU input, `poison_source=1` | Exactly one context whose complete 128-byte packet matches the control; source changed and all CPU packet words rechecked unchanged | Existing word-5/27 egress repair; peer captures exactly one original complete 128-byte frame |

The early experiment uses SSID `80000010` and the established temporary
Wi-Fi SPA attributes to reach queue 16. The late experiment uses SSID
`8000000f` and the established CPU metadata repair to reach WAN. These are
different downstream routes; the result is not a same-route latency
measurement. The early counterexample independently disproves the general
claim that RD=1 means this source's payload has already been read.

Files under `out/acceptance/sg-dma/`:

- `source-poison-np1.json`: first early counterexample. The old generic wire
  classifier says `WOE_EGRESS_BLOCKED`, as expected for a queue16-targeted
  experiment. Its `bad=1` is the old comparator noticing the intentionally
  changed payload, not a kernel fault. The explicit byte classification is
  the relevant evidence.
- `source-poison-np1-repeat.json`: independent fresh boot, same counterexample;
  new `SOURCE_READ_AFTER_RD_OBSERVED` diagnostic verdict, `bad=0`.
- `source-poison-held.json`: first late mutation, exact original WAN frame.
- `source-poison-held-repeat.json`: independent fresh boot;
  `SOURCE_POST_CPU_MUTATION_PASS`, exact original WAN frame.

The repeated runs used the final 1855-line probe. Its build with `W=1` and
strict checkpatch passed. A transient few-millisecond make timestamp warning
was rechecked with a clean subsequent build. The host classifier has explicit
source-lifetime verdicts; none of these verdicts advertises SG acceptance.

Example commands from the repository root, each on a fresh RAM boot:

```sh
python3 port/mainline/sg-acceptance/woe-wire-accept.py --wifi-attrs \
  --module-args 'descriptor_bp=4 poison_source=2 rxd_swap=1 sop_shift=29 unknown_sid=0 ssid_value=0x80000010'

python3 port/mainline/sg-acceptance/woe-wire-accept.py \
  --module-args 'descriptor_bp=5 cpu_steps=1 cpu_metadata=3 poison_source=1 rxd_swap=1 sop_shift=29 unknown_sid=0 ssid_value=0x8000000f'
```

## Source and callback audit

The locally available public WAV600 code is not proof of the ZTE WOE
integration contract. In its `hw_mmb.c`, `_mtlk_mmb_handle_dat_ind` checks
the Wi-Fi descriptor OWN bit, looks up a buffer by descriptor index, unmaps
the Wi-Fi receive DMA mapping, and handles the data. Its fragmented receive
path allocates a combined buffer, copies fragment bytes and frees the old
fragments. The refill path maps a replacement buffer, updates its payload
address, sets firmware ownership and rings the doorbell. These operations
establish that software path's Wi-Fi→host lifecycle, not completion of a
second ZTE WOE/IDM read of those bytes.

Relevant local source locations under
`out/acceptance/sg-dma/iwlwav-source/drivers/net/wireless/intel/iwlwav/wireless/driver/hw_mmb.c`:
OWN check 2231..2243; slot lookup 2273; unmap 2306..2310; fragment copy
2348..2416; replacement/map 2447..2476; ownership/doorbell 9040..9067.

Rechecked vendor `woe_rx_isr_init` (`np.ko`, `0x5434`) configures descriptor
queue thresholds, timers, interrupt clearing and PCIe pointer-update modes.
`woe_rx_ring_pointer_update` (`0x5638`) configures pointer writeback.
Neither recovered body establishes a payload-complete callback.

`net_check_reorder_rls_nolock` (`plat_132`, `0xb050`) polls release rings
and dispatches per-ring callbacks. The consumer that supplies the needed
source ownership semantics remains unrecovered; its existence cannot be
inferred from the callback slot. `register_woe_ser_recycle_handle` is a
separate recovery callback registration and is not evidence of per-packet
normal completion.

## Consequence for implementation

The later [release-ring contract audit](SG_RELEASE_RING_CONTRACT.md)
recovers the three registration APIs and four-byte ring storage, and checks
the complete factory module package for a missing consumer. No actual
release-record interpreter was identified in that package.

The 32-byte descriptor's observed source address/length and the two-stage
[ODMA association](SG_ODMA_FRAME_ASSOCIATION.md) now have a direct source
dependency test. What is still missing is a usable event after the relevant
IDM/NP2 source reads, together with its per-buffer ownership contract, plus
an actual multi-address continuation format. The debugger's CPU-packet
match is not a production completion API. Do not enable SG or release source
pages based on RD, ordinary TX completions, or these mutation passes.

## Final recovery

The original RAM FIT was reloaded after every submitted probe; no flash or
saved boot environment was changed. Final evidence in
`out/acceptance/sg-dma/recovery-source-ownership/` records:

- WAN 8 flows / 16 MiB / zero bad bytes; submitted/completed 816/816 and
  11183/11183.
- LAN 8 flows / 16 MiB / zero bad bytes; submitted/completed 708/708 and
  10984/10984.
- No loaded probe, pending TX, RX/TX errors, timeouts, refill deficit or
  kernel faults; temporary WOE/SPA/debug state cleared, console restored.
- Original host/board addressing preserved, RXCSUM on, SG off/fixed.
