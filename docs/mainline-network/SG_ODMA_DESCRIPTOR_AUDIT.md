# ODMA descriptor and PC1999 target audit

## Result

The later [controlled frame association](SG_ODMA_FRAME_ASSOCIATION.md)
separates the WOE NP1 descriptor pass from the ordinary WAN output pass and
records the corresponding completion boundaries. The idle observation below
remains unassociated background traffic.

Factory diagnostics expose multi-buffer-related fields in the MF input and
TM output descriptions, but no external scatterlist submission contract was
recovered. Live debug words are readable; the captured values do not identify
our 128-byte test frame. PC1999's target is still unknown: direct comparison
with the SPA port table did not establish a match.

This is a static decode plus read-only table/debug-register observation on
the normal mainline RAM boot. No SG flags, buffer pointers or descriptor
contents were changed, and no new probe was loaded.

## Register access and field provenance

The following `np.ko` functions were decompiled:

- `fpga_read_reg` at `0xdd3fc`: selector 2 dispatches to NPPT reads.
- `fpga_nppt_read_reg` at `0xdc668`: reads `nppt_base + 4 * word_index`.
- `odma_mf_48B_show` at `0x3f60c`: prints the MF input layout, including
  extra words for CPU type 133/129 despite the historical function name.
- `odma_to_tm_desc_show` at `0x3fdf8`: prints the TM output layout and seven
  reconstructed WOE descriptor words for CPU type 133/129.
- `odma_stats` at `0x3f2dc`: identifies input/read/output counters.

On this board NPPT is at `0x19000000`. The factory routines perform ordinary
register reads; they do not freeze a snapshot, select a packet identifier,
or read a validity generation. Software must therefore not assume atomicity
or that the input/output debug words describe the same packet.

| Debug word index | Physical address | Relevant factory field |
| --- | --- | --- |
| `0x5030` | `0x190140c0` | `pkt_start_pc` bits 16:1, `dup_send_end_flag` bit 0 |
| `0x5033` | `0x190140cc` | `multi_buf_num` bit 6, queue bits 15:7 |
| `0x5032` | `0x190140c8` | `pkt_global_num` bits 29:16, move bit 30 |
| `0x5034` | `0x190140d0` | destination port bits 7:2, packet offset bits 15:8 |
| `0x5036` | `0x190140d8` | `third_ptr` bits 31:18 |
| `0x5038` | `0x190140e0` | `second_ptr` bits 31:18 |
| `0x5039` | `0x190140e4` | packet length bits 31:18, NP2 length bits 13:0 |
| `0x503a` | `0x190140e8` | source port bits 31:26 |
| `0x503c` | `0x190140f0` | TM packet length bits 23:10, queue bits 9:1 |
| `0x503d` | `0x190140f4` | `first_ptr` bits 14:1, `np1_flag` bit 0 |

The first/second/third pointer fields are **14-bit values**, not complete
Linux DMA addresses. Their mapping, validity rules and mode-dependent use
are not established by their names. `multi_buf_num` is a one-bit field in
this printer, not a recovered segment-count format. The duplication-related
end flag cannot be relabeled as SG EOP without consumer evidence.

TM WOE descriptor words span registers `0x5040..0x5043` and `0x504a..0x504d`
with cross-word carry bits. The new offline
`port/mainline/sg-acceptance/odma-desc-decode.py` reproduces that packing and
the relevant field extraction. It never accesses the board. Example:

```sh
python3 port/mainline/sg-acceptance/odma-desc-decode.py \
  out/acceptance/sg-dma/odma-idle-raw.json
```

## Live observation and its limits

ODMA auto-gate is NPPT global table field `0x31`: register `0x190000b8`,
bit 13. It was saved, cleared for the reads and restored to its original
zero value. Two passes over the descriptor debug words and counters agreed:

- PPU input, SMCT-read and output-to-RED counters: 32803 each; ISU input 0.
- MF: length 60, source port 6, destination port 15, queue 415,
  `multi_buf_num=0`, second pointer 11788, third pointer 16383.
- TM: length 60, output list 15, queue 415, first pointer 3763.

These observations are saved in `out/acceptance/sg-dma/odma-idle-raw.json`.
They are not the known 128-byte test frame. In particular, a nonzero second
pointer while `multi_buf_num=0` must not be counted as a consumed second
buffer. It could be inactive or have mode-dependent meaning; this capture
does not resolve that. Two stable reads are useful for checking the decoder,
but do not prove packet attribution or descriptor atomicity.

## PC1999 versus SPA port attributes

The earlier response trace saw RSP4 become 0 and RSP7 become `0x40` after
PC1999 in the native case. We read all 32 SPA words for ports 0, 5 and 6,
using the factory-derived indirect read command, bounded ready polling and
saved/restored clock gate. The checked capture is
`out/acceptance/sg-dma/spa-full-port-audit.json`. An initial exploratory read
without explicit ready polling was superseded by this checked capture.

Nonzero port-6 words were:

```text
0=00000463  1=0000088f  10=01f4000c  13=00008008
17=0e014400 18=000001f1 19=148e0000
```

No direct raw-word correspondence with RSP7=`00000040` was found. This
does **not** exclude a transformed/subset port lookup or a different table;
it means that the proposed identification of PC1999 as SPA remains unproven.
Neither a generic payload DMA read nor a port-table lookup is established.

## Next gate and validation

The concrete observation interface is now mapped. Before another SG trial,
the debug data must be associated with a controlled frame (at least matching
direction/length and synchronized counter changes), and the validity plus
mapping of the 14-bit fields must be recovered. Extended WOE words must be
examined in the applicable mode rather than assuming that every field is
always active. This is not yet grounds to exclude every WOE path or enable SG.

The offline decoder passed CLI parsing of both actual read passes and checks
of 14-bit limits / 32-bit cross-word carry truncation. No kernel build was
needed for the offline tool. The table operations changed only indirect read
selectors and temporarily cleared/restored their clock gates; no table
contents, production features, saved environment or storage were changed.
