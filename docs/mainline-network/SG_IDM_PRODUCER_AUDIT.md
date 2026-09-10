# IDM TX producer audit — 2026-09-07

SG remains unimplemented. This read-only follow-up to
[the split wire tests](SG_WIRE_SPLIT_TESTS.md) checks whether the vendor CPU
producer exposes a continuation contract omitted by the current driver.
It does not add a hardware experiment or establish that the silicon lacks SG.

## Evidence and scope

The functions below were re-decompiled from `plat_132.ko.i64` and compared
with the recovered source where available. The corresponding `plat_132.ko`
SHA256 is `6cb3e7c9567b4549b5d1b1c6d87f502d3b07fcde0461c2d28a5ad115661d4d51`.
Addresses are IDA module addresses. This is a focused producer audit, not an
exhaustive enumeration of every descriptor writer in every vendor module.

| Producer or diagnostic | Address | Verified behavior |
| --- | --- | --- |
| `dump_tx_desc` | `0x148b4` | Names `idm_flag` as `(byte[27] >> 2) & 3`; this is descriptor word 6 bits 27:26. Prints one address and one 14-bit length. |
| `net_cfg_desc_by_skb` | `0xdf4c` | Clears word 6, then sets `idm_flag = 2`; initializes source and output metadata. |
| `net_tcp_gso_tx` | `0xf258` | Copies linear/fragments into a contiguous nbuf for each packet; uses `idm_flag = 2`. |
| `net_gso_upload_send` | `0xe634` | Sets `idm_flag = 3`; supplies one payload address plus repetition/MSS/remainder fields, not an address list. |
| `idm_cpu_tx` | `0x14a30` | Writes one skb data physical address and length; publishes one descriptor. Does not itself initialize `idm_flag`, so no default enum meaning is inferred from this function alone. |
| `idm_wifi_tx` | `0x14be4` | Likewise one data address and length, with Wi-Fi source metadata. |
| `cpu_net_nb_tx` | `0xcf14` | Walks software `nbuf->next`, detaches each node, obtains one descriptor per node, and copies nbuf words at offsets 48/52/56 into descriptor words 1/2/6. The next pointer itself is not submitted. |
| `idm_alloc_nbuf` | `0x14034` | Allocates one pool buffer and initializes pointers into that allocation; does not construct a DMA address list. |
| `idm_free_skb_data` | `0x14604` | Raw skb flag bit 15 selects an auxiliary buffer-address table release loop using the skb fragment count. This is a receive/GRO reclamation path, not a TX producer. |

## What this rules out, and what remains unknown

`idm_flag` values 2 and 3 cannot be presented as newly discovered SG
start/end flags: existing vendor producers use them for the two behaviors
above. Their full hardware enum semantics, especially value 1, remain
unrecovered. No experiment should assign an invented meaning to value 1.

`cpu_net_nb_tx` batching does not establish hardware frame grouping. It
does preserve caller-supplied word 6, so this audit also does not prove
that every caller uses only ordinary packets. A useful next static target
is the provenance of nbuf offset 56 in actual callers, followed by the
consumer of the corresponding descriptor control bits. A producer that
supplies multiple addresses, lengths and an explicit association would be
new evidence; a field name or unused enum value would not.

The next board experiment still needs an evidenced association mechanism
and an exact full-frame control. It need not assemble a contiguous buffer
internally, but the peer must receive one complete frame and the driver
must eventually establish safe completion/reuse for every mapped page.

## Cross-module follow-up: nbuf offset 56

The next audit opened the available `ipsec.ko` for static analysis (SHA256
`8f9f55ff133714961234dd5784dd7c08e26c739292a89d55c8c6a7d37827f0fb`).
Its five direct calls to `cpu_net_nb_tx` are in `mcpu_process_backlog`
(`0x3d8`, two calls) and `ipsec_code_done` (`0x1ae8`, three calls).
The following assignment chain is now recovered:

```c
/* Raw offsets; entry/template field names remain untyped. */
template = *(void **)(entry + 400);
*(u32 *)(nbuf + 48) = ((u32 *)template)[1];
*(u32 *)(nbuf + 52) = ((u32 *)template)[2];
*(u32 *)(nbuf + 56) = ((u32 *)template)[user_off];
/* plat_132: cpu_net_nb_tx */
tx_descriptor[6] = *(u32 *)(nbuf + 56);
```

This is present in `ipsec_code_done`, `mcpu_process_backlog`'s linked-node
loop, and `ipsec_no_crypt_auth` (`0x5dc`). ARM64 instructions at
`0x1dc0..0x1dc8` and `0x1e24..0x1e2c` independently verify the pointer,
indexed load and offset-56 store. The bounded linked-node loop copies the
same template control word to successive nodes; it does not assign distinct
start/end control values there. Filtered disassembly and assignment evidence:
`out/acceptance/sg-dma/ipsec-nbuf-provenance.json`.

`user_off` has file initializer 3. `zx_ipsec_init` (`0x2494`) changes it to
6 only on the `zte_api_version() == 0` branch. Neither a live branch nor a
live template value has been established. The source template's word 3 on
one branch must not be confused with the destination descriptor's word 6.

`esp_encode_step2` (`0x2bfc`) has a packet-length/MTU comparison and calls
the imported `ipsec_frag` on the oversized-packet branch. This supplies a
concrete IP-fragmentation context for further list research; the imported
function body is unavailable here, so it does not by itself establish the
contents of every linked list. It certainly is not proof of Ethernet SG.

The symbol audit of all 27 available `.ko` files finds only `ipsec.ko`
importing `cpu_net_nb_tx`, with `plat_132.ko` defining it. No definition of
`_update_entry_data`, `find_dn_entry` or `ipsec_register_func0` was found in
those modules or the symbols of `vendor-reference/2b5/kernel-2b5.elf`.
These names are also absent from the archived runtime kallsyms, and that
runtime's `/proc/modules` does not list `ipsec`. This bounds the available
evidence; it does not prove that every factory firmware lacks those bodies.
In particular, the writer of `entry +400` and its template is still missing;
the name `_update_entry_data` alone does not prove it is that writer.

### Connection to the already measured CPU-input branch

Current native TX stores `0x08000000` in `desc->metadata[4]`, which is
descriptor word 6. It therefore matches the vendor diagnostic's numeric
`idm_flag = 2`. The earlier CPU-input trace measured control value
`0x08000000` in packet word 27 / SPR9 and a changed successor at PC1940;
the later persistent metadata repair passed exact whole-frame WAN egress.
This connects the recovered producer value to existing consumer evidence.
It does not establish a complete bit-for-bit transform for all input modes,
decode the whole flag enum, or reveal a continuation branch. No fresh
microcode execution or new instruction-decoder result is claimed here.

The concrete remaining boundary is now **the session template producer and
the consumer of any additional association fields**, rather than the
location of the offset-56 assignment. Repeating flags 2/3 on separate
buffers would repeat known packet/template modes, not test a recovered SG
contract. No grounded new board candidate emerged from this follow-up.

## Work and board state

The subsequent [PPU consumer trace](SG_PPU_CONSUMER_TRACE.md) connects the
observed control branch and local address-value reads to exact firmware PCs,
and separates PPU context end from the ordinary IDM completion consumer.

The initial audit changed documentation only; the cross-module follow-up
also saved the filtered static evidence JSON. No production or probe source
was changed. No kernel/module build was
needed or run; no module was loaded, no registers were written, and no
network acceptance test was rerun. A read-only serial capture showed the
previous normal Linux recovery session. The WAN/LAN regression results in
the split wire report belong to that preceding recovery, not a new run.
Production SG remains disabled; implementation is blocked on the missing
multi-address frame association and ownership contract.
