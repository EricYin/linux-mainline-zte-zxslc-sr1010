# IDM release-ring registration and missing consumer contract

## Result and material boundary

The three registration APIs, callback arguments, ring storage stride and
acknowledgement registers are recovered. The code that interprets release
records and identifies the resource to free is **not present as an identified
caller/consumer in the available module set**. Neither record endianness nor
the meaning of its 32 bits can be inferred from the generic poller.

This audit covered the saved modules and the complete module image embedded
in the factory root filesystem. It did not merely stop at the running-module
inventory. No dummy callback, acknowledgement or new board experiment was
introduced because no valid record/resource contract was recovered.

## Registration and dispatch

Addresses below are IDA module addresses in `plat_132.ko`.

| API | Address | Slot written |
| --- | --- | --- |
| `register_woe_recycle_handle` | `0xaf90` | `idm_recycle_cb[0]`, IDA `0x27930` |
| `register_woe1_recycle_handle` | `0xaf9c` | slot 1, `0x27938` |
| `register_woe2_recycle_handle` | `0xafa8` | slot 2, `0x27940` |

Each setter stores the supplied function pointer and returns it. It accepts
null; it does not establish ownership or interpret a release record.
The setters have no direct in-module callers. Their slot xrefs identify the
setters; the poller also accesses the slot array through indexed addressing.

`net_check_reorder_rls_nolock` (`0xb050`) calls a non-null slot as:

```c
callback(queue_number, &temporary_context);
/* temporary_context raw offsets on this AArch64 binary: */
/* +0: 64-bit ring virtual base */
/* +8: 32-bit ring capacity */
/* +12: 32-bit software read index */
/* +16: 32-bit number of records offered */
```

The context is stack-local; its pointer must not be retained. The poller
caps each reported count at 4095. After invoking a callback, it advances
that ring's software read index by the count, masked with capacity minus 1.
It does not itself inspect the record values or free a source buffer.

**A null callback does not prevent hardware acknowledgement.** In that
case the poller skips callback/index advancement, but still passes the count
array to `idm_rls_update` at the end. Copying this polling path into a driver
without a real consumer could discard notifications. That is not an
acceptable substitute for implementing source ownership.

## Ring layout and acknowledgement

`cpu_net_init` selects capacities from the normal, jumbo and extra retrieval
counts and enables three rings for CPU type 133/129. `idm_init` reserves
`4 * capacity` bytes for each retrieval ring; its base arithmetic establishes
a **four-byte storage slot**, not the semantic type of that word.

| Ring | IDM base register | Pending-count register | Acknowledgement |
| --- | --- | --- | --- |
| 0 | `+0x118` | `+0x08c` | count in low half of `+0x07c` |
| 1 | `+0x11c` | `+0x0f8` | count shifted left 16 into `+0x07c` |
| 2 | `+0x408` | `+0x41c` | count written to `+0x418` |

`idm_get_reorder_rls` (`0x138f8`) reads these pending-count registers;
`idm_rls_update` (`0x13740`) implements the writes, gated for the applicable
CPU types. These are consumable pending counts, not the ordinary TX done
sequence counter. No acknowledgement was issued during this audit.

Normal/jumbo/extra configuration names do not prove that records are normal
BMU indices, Linux addresses, Wi-Fi buffer tokens or skb pointers. That
distinction belongs to the missing consumer and hardware producer.

## Exhaustive check of the available module package

- All 49 `.ko` paths under `vendor-reference` were hashed and deduplicated:
  28 distinct ELF binaries.
- Their ELF symbol tables contain definitions of the three registration
  APIs only in `plat_132.ko`, and no undefined imports of those APIs.
- The available `kernel-2b5.elf` contains none of the three exact symbol-name
  strings. Archived kallsyms contains the exported setters and callback
  array, but is not a dump of the callback slot contents.
- The factory `rootfs.jffs2` contains `/kmoduletmp/kmodule.img` (inode 496).
  Its 4944 inode fragments reconstruct a 19,673,088-byte SquashFS image.
  Its 38 regular module files deduplicate to 28 binaries; all hashes match
  already saved modules. There is no additional module in this package
  that supplies a previously missed direct importer.

The package inventory and per-file hashes are preserved in
`out/acceptance/sg-dma/release-rootfs-module-audit.json`. Its reconstructed
module-image SHA256 is
`3fbb2e29fe7a94124aba084217c372d2694c880c8657781c9c641f7d0d1fad46`.
Reconstruction and extraction used a temporary analysis file; the original
firmware image and saved module files were not changed.

These results bound the available evidence. They do not prove that a numeric
or dynamically resolved call is impossible, that callback slots were always
null on every factory runtime, or that other firmware variants lack an
integration module. The exact missing filename is unknown. An actual ZTE
WOE/WAV600 integration module that calls these registration APIs, or source
defining the release record and its hardware completion semantics, would
advance this specific line of investigation; a generic public WAV600 driver
does not supply that contract.

## Consequence for SG and current board state

The earlier [source mutation counterexample](SG_SOURCE_OWNERSHIP_TESTS.md)
still excludes descriptor RD as payload completion. This audit does not
replace it with a newly validated event. A four-byte release ring and a
callback name are insufficient to enable SG or safely free DMA mappings.

This continuation performed static/module-package analysis only. It did not
load a module, change board registers, acknowledge rings, or rerun network
acceptance. The preceding source-ownership recovery remains the latest board
acceptance record; no fresh runtime state is claimed from this static audit.
