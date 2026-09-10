# Controlled ODMA frame association and ownership boundaries

This continuation modified the RAM probe, corrected a peer-side source of
interference, and ran five full-frame experiments through build, RAM boot,
capture, analysis and final WAN/LAN recovery. SG remains unimplemented.

## Fixture changes and rejected observations

The probe reads ODMA counters and descriptor debug words twice at each
observation point, preserving the ODMA clock gate. It records ordinary IDM
TX done counts as well. Later revisions add an early post-continue snapshot
and a snapshot while the exact matching CPU context is still stopped.

The host wire tool now binds UDP `192.168.1.100:49030` for the experiment
and closes it in `finally`. Previously the unbound destination could provoke
an ICMP port-unreachable response and overwrite the ODMA debug words. This
does not change the host address or interface topology.

Evidence files below are under `out/acceptance/sg-dma/`:

| File | Wire result | Association result |
| --- | --- | --- |
| `odma-native-control.json` | Exact control frame | Rejected: ODMA counters +2; last output describes reverse traffic. |
| `odma-native-quiet.json` | Exact control frame | Accepted bounded association: counters +1, 128-byte 15→6 descriptor, ordinary IDM completion +1. |
| `odma-woe-quiet.json` | Exact control frame | Rejected: counters +28 and reverse traffic in late sample. |
| `odma-woe-early.json` | Exact control frame | Early/late snapshots agree on 128-byte WAN output, counters +2, ordinary IDM completion unchanged. Motivated separating two stages. |
| `odma-woe-held.json` | Exact control frame | Separates one ODMA operation before CPU continue and one after it; detailed evidence below. |

All five controls were complete 128-byte frames and capture calibration
passed. None submitted split payloads. Invalid association runs remain in
the record and are not promoted to successful descriptor captures.

## WOE has two observed ODMA stages

In `odma-woe-held.json`, WOE source payload is at `0x9bc61000`. The probe
matches all 128 expected bytes at CPU input before modifying the two already
validated egress metadata words. Both read passes agree at each stage.

| Observation | PPU-in / SMCT-read / RED-out counters | MF/TM length | NP2 length | Destination | `np1_flag` | First / second / third pointer |
| --- | --- | --- | --- | --- | --- | --- |
| Before submission | 390 / 390 / 390 | Background calibration | — | — | — | — |
| CPU held at PC1924 | 391 / 391 / 391 | 32 / 32 | 128 | 15 | 1 | 2375 / 10568 / 16383 |
| After CPU continue | 392 / 392 / 392 | 128 / 128 | 0 | 6 | 0 | 2379 / 10572 / 16383 |
| Later snapshot | 392 / 392 / 392 | Same as preceding row | 0 | 6 | 0 | Same |

The held stage's MF start-PC field is 5174 and source-port field is 49.
The outgoing ordinary stage has start PC 0 and source port 15. The exact
wire frame and intervening held context provide a stronger association than
an idle debug-register dump, though individual debug reads are not hardware
atomic snapshots.

This narrows the remaining payload-read investigation: the first ODMA
operation carries the 32-byte NP1 descriptor, and the matching CPU-input
context already contains the complete 128-byte packet. Combined with the
IDM NP2 counters and the earlier recovered NP1 descriptor, the intervening
IDM/NP2 path is the relevant consumer to investigate. The external DMA bus
request itself was not captured, so this is stage localization rather than
an instruction-level DMA contract.

## The 14-bit fields are not a recovered external scatterlist

Both associated WOE stages have `multi_buf_num=0`. The valid native
128-byte output likewise has this flag zero, with first pointer 2435 and
second pointer 10628. For these three samples:

```text
second_ptr - first_ptr = 8193 = 0x2001
third_ptr = 0x3fff
```

That is a measured relationship, not proof that bit 13 is a validity bit or
that the remaining bits are a normal-BMU index. No second external DMA
address or per-segment length follows from it.

The CPU-input packet word 9 was `a5240000`; the held ODMA raw word labelled
`second_ptr` was `a5200000`, and the outgoing one was `a5300000`. They are
not equal to the external WOE source address `9bc61000`. Earlier descriptions
of the PC1977 load as an "address-value read" should be read as loading this
opaque packet pointer word, not as a verified physical DMA address.

The factory `tm_bmu_init` (`np.ko`, `0x70db0`) initializes normal/jumbo
BPPE rings with sequential big-endian 16-bit indices and programs their pool
bases and strides. Live readback in this experiment was:

```text
normal pool base  9f120000     normal stride 0840
jumbo pool base   9fd80000     jumbo stride 2600
descriptor base   9fdcc000     normal count 1800
```

A bounded post-experiment check of the first 256 bytes of normal-pool
candidates 2375..2379 found neither the complete test frame nor its marker.
This does not disprove a mapping (the packets had completed and buffers could
have been reused), but it supplies no support for mapping the 14-bit fields
directly through the normal-BMU pool. No speculative writes to these pools
or pointer fields were performed.

## Completion paths remain distinct

The accepted native run increments its ordinary IDM TX queue completion by
one. The separated WOE run leaves all four ordinary TX completion counters
unchanged through both ODMA stages. The WOE source-release counters also
remain zero. Exact WAN delivery is therefore not enough to reclaim the WOE
source with the ordinary netdev completion mechanism.

Rechecked `woe_rx_ring_pointer_update` (`np.ko`, `0x5638`) configures a
PCIe read-pointer writeback address and hardware updater modes; this is a
separate vendor mechanism from ordinary IDM TX completion. Its configuration
alone does not establish when external payload reads finish. No new updater
mode was enabled or described as a proven source-release event.

Rechecked `idm_stat` distinguishes AXI request/ack pairs at IDM `+0x198`,
NP1→NP2 SIPC sends at `+0x218`, and length-status pairs at `+0x224/+0x228`.
These are aggregate counters, not a recovered continuation-descriptor
consumer. Additional counter changes alone would not authorize SG mapping
or page reuse.

## Validation and recovery

The final 1786-line probe passed the ARM64 module build with `W=1` and
strict checkpatch with zero errors, warnings or checks. The peer tool's UDP
binding was exercised by the subsequent four wire runs. The offline ODMA
decoder now accepts the staged probe logs as well as the earlier idle log.

Final original RAM FIT reload verified 10,997,052 bytes. Evidence under
`out/acceptance/sg-dma/recovery-odma-association/` records:

- WAN: 8 flows, 16 MiB, zero bad bytes; queue submitted/completed
  702/702 and 11306/11306.
- LAN: 8 flows, 16 MiB, zero bad bytes; queue submitted/completed
  477/477 and 11271/11271.
- No probe loaded, no pending TX, no RX/TX errors, no timeouts, no refill
  deficit, no kernel fault output; breakpoint state cleared and RXCSUM on.
- WOE controls and temporary Wi-Fi SPA state cleared by the fresh boot;
  host addressing/topology preserved, no storage or saved environment writes.

The actionable unresolved boundary is the IDM/NP2 external-buffer consumer
and WOE source ownership, not locating ODMA's descriptor registers. The
observed first/second/third fields do not yet supply a Linux SG submission
format. Production SG stays off.
