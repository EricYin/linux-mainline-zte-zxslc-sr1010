# Ordinary IDM native chaining audit

Follow-up: [TSO/TSO6 hardware feasibility](TSO_HARDWARE_FEASIBILITY.md) audits
the GSO upload template separately from scatter-gather and records its
zero-payload construction, descriptor parameters and remaining consumer gates.

## Verdict

**No implementable native SG/chaining contract was found in the reviewed
ordinary CPU→IDM transmit paths.** All reviewed producers submit one data
address and one length per 32-byte descriptor. The known batching mechanism
submits independent descriptors; it does not encode association into one
Ethernet frame. No grounded register/descriptor candidate emerged for a new
board experiment.

This is not proof that the silicon has no undocumented chain mode. It is a
negative result for the available producer/configuration evidence. Ordinary
TX's known completion path also cannot automatically be extended to an
unverified chain mode.

## Coverage and primary evidence

The audit rechecked `plat_132.ko.i64`, the recovered source and the current
mainline driver. `plat_132.ko` SHA256:
`6cb3e7c9567b4549b5d1b1c6d87f502d3b07fcde0461c2d28a5ad115661d4d51`.
Addresses below are IDA module addresses.

`net_get_next_txdesc` has eight direct call sites in five functions:

| Caller | Function address | Call sites |
| --- | --- | --- |
| `cpu_net_nb_tx` | `0xcf14` | `0xcfc0` |
| `idm_net_tx` | `0xd234` | `0xd334` |
| `cpu_net_tx` | `0xd668` | `0xd72c`, `0xd980`, `0xdbac` |
| `net_gso_upload_send` | `0xe634` | `0xe664` |
| `net_tcp_gso_tx` | `0xf258` | `0xf5f0`, `0xf710` |

The low-level `idm_cpu_tx` (`0x14a30`), `idm_wifi_tx` (`0x14be4`) and
`idm_omci_tx` (`0x1493c`) are referenced through the IDM operations table.
Their data/length writes were checked together with the higher-level
descriptor setup, not treated as complete initialization routines alone.

An ELF symbol scan over all 28 distinct saved module binaries checked
imports of these transmit/reservation APIs. Its only cross-module import
among the checked names is `ipsec.ko → cpu_net_nb_tx`. That producer was
already traced through its session template in
[the producer audit](SG_IDM_PRODUCER_AUDIT.md). It supplies no recovered
external SG address-list contract. This scan does not exclude arbitrary
dynamic symbol lookup or opaque external callback behavior.

## Descriptor fields: known use versus unknown space

| Word | Established producer use | Chaining evidence |
| --- | --- | --- |
| 0 | One 32-bit data address from skb/nbuf data | No second source or next-descriptor pointer |
| 1 | 14-bit length in bits 14:1; source selection and checksum-related fields elsewhere | No reviewed producer sets a recovered SOP/EOP association |
| 2 | Output-port and protocol/checksum metadata | No recovered list pointer |
| 3 | No chain-pointer write identified in the reviewed producers | Remaining semantics not established |
| 4–5 | Cleared on reviewed ordinary paths; GSO upload uses count/MSS/remainder fields in this area | Template parameters, not an address array |
| 6 | GEM/queue metadata and `idm_flag` in bits 27:26 | Known values 2/3 belong to ordinary/template paths; full enum remains unknown |
| 7 | No explicit next-pointer or segment-length write identified in the reviewed producers | Undocumented space is not proof of chain support |

`cpu_net_tx`'s ordinary setup explicitly sets word 6 to `08000000` and
clears words 4/5. `cpu_net_pon_set_desc` (`0xd5ac`) does the same while
adjusting queue/port metadata. The OMCI path uses one address/length with
management metadata. `cpu_lowpower_tx` (`0xd49c`) invokes an external
low-power hook with one pointer/length and can adjust packet length; no
chain construction is present in its recovered body.

No semantic names are assigned here to unused-looking bits, the complete
`idm_flag` enum, or untouched descriptor words. Those require new evidence,
not a bit sweep on a live DMA engine.

## Queue organization and doorbell semantics

`idm_init` configures four TX queues with fixed 32-byte strides. It zeros
the TX descriptor area and gives each queue a separate software owner table.
Relevant programming includes:

```text
IDM +004 : contiguous TX descriptor area base
IDM +00c : uIDM_TX_CFG_DEPTH << 16
IDM +074 : 00000210 (raw factory value; full semantics not decoded here)
```

`net_get_next_txdesc` returns `base + producer * 32`, increments the
producer and wraps at queue depth. It does not follow a pointer in the
preceding descriptor. This establishes ring traversal, not frame boundaries.

`idm_cpu_nb_tx_update` (`0x137c4`) issues `DSB ST` and writes `count << 17`
to the queue doorbell (`+080` for queue 0, otherwise `4 * (queue + 39)`).
`cpu_net_nb_tx` detaches each software `nbuf->next`, reserves one descriptor,
stores one tagged owner, and publishes accumulated counts after batches.
No next pointer itself is submitted. `cpu_net_nb_desc_tx` (`0xdfa8`) stores
one owner and publishes one descriptor. Neither path provides a recovered
"these descriptors belong to one frame" marker.

The raw queue-control constants may contain undocumented capabilities.
Their existence does not establish an alternate descriptor format, and the
audit does not reinterpret queue depth/fetch configuration as chain enable.

## GSO does not close the gap

`net_tcp_gso_tx` walks the skb's linear/fragments and copies bytes into
contiguous output nbufs before submitting them. The resulting packets are
individually constructed segments.

`net_tcp_gso_tx_upload` (`0xec3c`) prepares a contiguous nbuf/template;
`net_gso_upload_send` sets `idm_flag=3` and count/MSS/remainder information
while still supplying one address. The alternate upload body also creates
individual contiguous outputs. These observed modes are not arbitrary
multi-address SG, and their presence is not a reason to enable TSO here.

## Completion contract and current driver

The ordinary completion consumer takes the 16-bit done-count delta and
advances one owner-ring slot per count. Each reviewed submission stores one
skb or tagged nbuf owner per descriptor. There is no reviewed per-fragment
DMA mapping array or "free only on final segment" group completion state.

If a real hardware chain mode were discovered later, its counter unit and
error handling would still need calibration. A chain could complete per
descriptor or per frame; assuming either without evidence could release
pages too early or leave mappings pinned.

The current mainline `zx279133-netdev.c` calls `skb_linearize`, maps the
result with `dma_map_single`, clears/fills one descriptor and records one
owner. That matches the established ordinary TX contract. It was not
changed during this audit. Setting `NETIF_F_SG` would not fill the missing
hardware frame-association or completion contract.

## Outcome and validation scope

The static audit covers the known descriptor reservation call sites,
low-level operations, ordinary/management/template setup, queue publication
and owner reclamation. It found no native chain producer or documented
chain-enable configuration. The practical status of ordinary IDM SG is
**unproven, with no justified implementation experiment yet**, rather than
"available once the driver maps skb fragments".

This audit made documentation/evidence changes only. No production/probe
source was modified, no module was loaded, no board registers were written,
and no build or network acceptance was rerun. Earlier RAM recovery remains
the latest board acceptance evidence; SG stays disabled.
