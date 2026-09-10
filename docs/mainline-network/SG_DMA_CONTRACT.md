# SG DMA contract investigation

Status: **BLOCKED / not implemented**, explicitly accepted by the user on
2026-09-07. Active work moves to RSS/RX multiqueue; SG is no longer a prerequisite
for that work. `NETIF_F_SG` remains disabled.

The three audited paths have no implementable contract yet: WOE lacks frame
continuation and source release; ordinary IDM has no established native chain
format; independent DMA aggregation lacks an identified controller interface
and completion protocol. Resume SG when new register/IP documentation, usable
factory code, or controlled evidence establishes one of those missing contracts.
Clock names alone are not a sufficient reason to resume speculative DMA probes.
See [native IDM audit](SG_NATIVE_IDM_AUDIT.md),
[source ownership tests](SG_SOURCE_OWNERSHIP_TESTS.md), and
[independent DMA audit](SG_INDEPENDENT_DMA_FEASIBILITY.md).

The user requested hardware scatter-gather for ordinary
CPU Ethernet TX and confirmed that only `vendor-reference` binaries are available.
This investigation recovered a configurable WOE descriptor input and has now
verified a complete 128-byte Ethernet frame moving from CPU-owned DMA memory
through NP1/NP2 into IDM queue 16. Three repeated complete frames matched every
byte. The flat Intel format rejected two noncontiguous 64-byte fragments with
`0x62/0x10`. A subsequent separate-DMAD/RXD experiment reads both fragments
correctly, but returns two 64-byte queue-16 records rather than one frame there.
Acknowledging those queue records produces no source-buffer release completion.
The hardware gather and completion contract is still missing.
`NETIF_F_SG` stays disabled. These findings do not establish that the silicon
lacks SG.

**Wire-boundary clarification:** SG does not require a contiguous intermediate
buffer or a single queue-16 record. Sequential fragment DMA and transmission
within one Ethernet frame would satisfy the payload requirement. The latest
[WOE egress acceptance](SG_WOE_EGRESS_ACCEPTANCE.md) now passes the complete
128-byte wire control on three independent RAM boots. Writing packet-memory
word 5 to `00008018` and word 27 to `08000000` is sufficient for the tested
case. This still uses per-packet debug intervention; the normal submission
format, fragment wire boundary and source completion remain unresolved.

The later [wire split matrix](SG_WIRE_SPLIT_TESTS.md) combines that successful
whole-frame control with both noncontiguous inputs in one batch. Six captures
all pass the control but none yields the joined frame. Complete RXD flags
emit the exact 64-byte head separately; candidate SOP/EOP flags produce two
NP1 `0x62/0x10` diagnostics. Alternate LS placement, preserving WOE control
bit 20 and simultaneous context release do not establish SG. A multi-buffer
association/continuation protocol is still missing.

The subsequent [CPU-input single-step comparison](SG_CPU_EGRESS_TRACE.md)
locates the first divergence at PC1940 and verifies the effects of control
bit 27 and the WAN-port operand. Scalar-only replay followed the same 48
observed PCs as native TX without reaching WAN. The later successful
packet-memory writes supersede that egress blocker; SG stays disabled.

The latest [factory-breakpoint captures](SG_DESCRIPTOR_BREAKPOINTS.md) now
show the actual single-buffer NP1 descriptor and its NP2 exception exit.
Restoring and resuming the real instruction preserves exact payload delivery.
The captured descriptor still provides no established multi-buffer sequence
or source-release rule, and the repeated split test still does not gather.

## Sources and reproduction

Factory modules are in
`vendor-reference/sr1010-vendor-runtime/modules/files/kmodule/`.
`np.ko` SHA-256:
`b7b5f4c3a1ec5ec14f62f3c4b21282bf68e4622fb3000f448ba4319a806ec986`.
Function addresses below refer to the existing IDA databases for these modules.

The standalone extractor reads ELF section and symbol tables, without IDA,
third-party Python packages, or hardware access:

```sh
python3 port/mainline/vendor-reg-table.py \
  vendor-reference/sr1010-vendor-runtime/modules/files/kmodule/np.ko \
  woe_reg_table --base 0x19000000 \
  --ids 0 0x14f 0x151 0x152 0x153 0x168 0x169 0x16a \
        0x16b 0x16c 0x16d 0x1a3 0x1a5 0x1a6 0x1a8
```

The table contains 444 entries of 28 bytes. Its raw ELF file offset is
`0x1920d8`; its IDA address is `0x192070`. These addresses are different and must
not be substituted for one another. The extractor resolves the symbol rather
than hardcoding either address.

`np_onu_reg_write @ 0xdd544` implements the entry layout:

| Byte offset | Field |
| --- | --- |
| 0 | u16 field ID |
| 4 | u32 access kind; write rejects 1, read rejects 2 |
| 8 | u32 register word offset |
| 12 | u32 unshifted mask |
| 16 | u16 shift |
| 18 | u16 maximum array index |
| 20 | u32 array stride in register words |
| 24 | u32 usage counter |

For NPPT bank 2, physical address is
`0x19000000 + 4 * (word_offset + index * stride)`. A write performs RMW using
`mask << shift`. Access value 3 appears in the fields below. Names and semantics
must still be recovered from callers; the table itself only gives addresses and
bit fields.

## Ordinary CPU IDM input

| Factory function | Evidence |
| --- | --- |
| plat `cpu_net_nb_tx @ 0xcf14` | Walks nbuf `next` pointers, clears each link, and submits one descriptor for each complete nbuf packet. The submitted count is a batch of packets, not a segment count for one packet. |
| plat `idm_alloc_nbuf @ 0x14034` | Allocates one contiguous buffer and initializes its data pointer and headroom. It does not construct a DMA scatterlist. |
| plat `idm_cpu_tx @ 0x14a30`, `idm_wifi_tx @ 0x14be4` | Recovered ordinary submissions supply one payload address and length. |
| plat `net_tcp_gso_tx @ 0xf258` | Copies the skb linear area and fragments into contiguous nbuf packets as part of software segmentation. |
| plat `net_tcp_gso_tx_upload @ 0xec3c` | Builds a header plus one MSS-sized cleared payload template. The upload count/MSS fields do not establish arbitrary payload SG or ordinary TSO. |

No second payload address, external scatterlist pointer, per-segment length
array, or multi-descriptor packet completion rule has been recovered for this
input. Unexplained descriptor bits are not sufficient authorization to invent a
DMA format. BMU/ODMA internal 14-bit buffer handles and fixed reserved pools also
do not establish mappings of arbitrary Linux pages.

## WOE receive descriptor input

The new candidate is **WOE receive input**, distinct from ordinary CPU IDM TX.
`woeRxSetupMT7915 @ 0x5bdc` explicitly configures:

- last-section bit in descriptor word 1, bit 30;
- SDP0 buffer address starting at descriptor word 0, bit 0;
- descriptor mode 1;
- DMA descriptor and RX descriptor byte counts/intercept lengths from config.

`woeRxSetupZTE780 @ 0x5c60` instead selects LS word 3 bit 16 and SDP0 word 4
bit 0, with descriptor extraction bitmaps `0xff` and `0x0f`.
These chip-specific settings prove configurable descriptor field extraction.
They do **not** by themselves prove gathering arbitrary Ethernet payload pages.
In particular, the recovered byte counts describe DMAD/RXD records, not each
skb fragment's payload length.

| Field IDs | Physical register | Fields |
| --- | --- | --- |
| 0 | `0x19204000` | RX enable bit 1 |
| 1, 2 | `0x19204000` | TX enable bit 0; common WOE enable bit 2 |
| `0x14f` | `0x19205000` | RX ring enable mask, bits 5:0 |
| `0x151..0x153` | `0x19205008` | DMAD bytes bits 18:10; RXD bytes bits 9:1; mode bit 0 |
| `0x154..0x155` | `0x1920500c` | RXD intercept bits 15:8; DMAD intercept bits 7:0 |
| `0x168..0x16a` | `0x19205044` | LS enable bit 31; bit index bits 12:8; word index bits 7:0 |
| `0x16b..0x16d` | `0x19205048` | SDP0 enable bit 31; bit index bits 12:8; word index bits 7:0 |
| `0x1a3` | `0x19205194 + 4*q` | Ring depth, low 16 bits |
| `0x1a5` | `0x192051e8 + 8*q` | Ring write pointer |
| `0x1a6` | `0x192051ec + 8*q` | Ring read pointer |
| `0x1a8` | `0x192052a8 + 4*q` | Descriptor ring start address |

Array entries allow index 6 in the table, but the RX enable mask is six bits and
`woe_get_phyaddr_of_woe_rx_dmadring_wr @ 0x4e2c` handles only ring IDs 0 through 5.
Do not infer a seventh usable receive ring from the table limit.

`woe_rx_dmad_ring_init @ 0x4d44` installs an external descriptor ring address,
depth and WOE ring-to-band selection. `woe_rx_ring_pointer_update @ 0x5638`
supports several producer/consumer update modes, including PCIe peer registers.
`woe_rx_ring_ser_part1 @ 0x13634` drains through descriptor-entry, SIPC SOP/EOP,
pointer-command and pointer-data counters before disabling the ring. Thus a
read-pointer advance alone is not established proof that all payload DMA reads
are finished and Linux pages may be unmapped.

The parser expects `/wlan/woe_global_cfg.dat` or
`/etc/woe/woe_global_cfg.dat` (`woe_get_cfgpath @ 0x5e10`,
`woeParseGlobalCfg @ 0x65cc`). Neither file was found in the collected vendor
reference inventory. Current mainline loads `mcode_intel.bin`; compatibility of
its PPU receive parsing with a synthetic MT7915-style CPU scatterlist remains
unproven.

### Initial read-only board observations

Observed on the existing RAM FIT
`8c74959267c363986cc59e6dcca216204857cce6198ddff580bda57a5beb4e53`.
At this initial stage, no WOE register writes, new image boot, or hardware SG
packet test had been performed. The later parser experiment is recorded below.

| Register | Value |
| --- | --- |
| `0x19204000` | `0x00000000` |
| `0x19205000` | `0x00000000` |
| `0x19205008` | `0x00004041` |
| `0x1920500c` | `0x00002010` |
| `0x19205044` | `0x80001e01` |
| `0x19205048` | `0x80000000` |
| `0x19205194` | `0x00000fff` |
| `0x192051e8` | `0x00000000` |
| `0x192051ec` | `0x00000000` |
| `0x192052a8` | `0xffffffff` |

WOE RX and its rings are disabled. The descriptor extraction settings match
MT7915 LS/SDP0 configuration; DMAD and RXD sizes decode as 16 and 32 bytes.
These are inactive register settings, not evidence of successful SG traffic.
`pon_woe1_wclk` was enabled at 416666666 Hz.

### RAM descriptor parser experiment

Source: `port/mainline/sg-acceptance/woe-rx-probe.c`. This is an external diagnostic
module, not part of the production Ethernet driver or FIT. It requires `arm=1`,
disables SIPC RX, and pins its DMA allocation until reboot. There is deliberately
no unload callback. Ordinary networking must only resume after reloading the RAM
FIT. Do not load it during normal router use.

The probe allocates a 64 KiB coherent arena on the Ethernet platform device.
Its eight-slot ring uses the local Linux `mt76/dma.h` descriptor ABI: SDP0 at
word 0, length at word 1 bits 29:16, LS0 at bit 30 and DMA_DONE at bit 31.
Slot 0 has LS=1; slots 1 and 2 have LS=0 and LS=1. All buffer pointers point
inside the retained allocation, with separate 4 KiB-spaced ranges. DMAD and RXD
extraction remain 16 and 32 bytes. PCIe producer polling, peer pointer updates,
and interrupts are disabled. This examines descriptor handling; no Ethernet
payload integrity or SG completion result is claimed.

The first probe omitted common WOE enable bit 2 and consumed no descriptors.
`woe_enable @ 0x170` revealed the missing step: ring enable, TX/RX enables, then
`woe_set_enable @ 0x168a4`, which writes table field ID 2. The corrected probe
enables the receive path and the common gate; the unrelated TX path stays off.

Measured on the same FIT after a fresh RAM boot:

| Stage | RD / WR | Enter descriptors | EOP records | Slice count | WOE-to-SIPC SOP / EOP |
| --- | --- | --- | --- | --- | --- |
| Before | 0 / 0 | 0 | 0 | 0 | 0 / 0 |
| One descriptor, LS=1 | 1 / 1 | 1 | 1 | 0 | 1 / 1 |
| Add two descriptors, LS=0 then LS=1 | 3 / 3 | 3 | 3 | 1 | 3 / 3 |
| Common gate disabled | 0 / 3 | 0 | 0 | 0 | 0 / 0 |

Ring DMA address was `0x9bc30000`; the first three SDP0 values were `0x9bc31000`,
`0x9bc32000`, and `0x9bc33000`. Raw diagnostic registers `0x19205808` and
`0x1920580c` changed from `0/0` to `0/0x50`; their complete interpretation has not
been recovered. The first log called these values `err`; the corrected source
uses `diag` to avoid treating unknown status bits as errors.

This proves descriptor consumption and recognition of a non-final section. The
two-section submission produces **two** records at the observed WOE/SIPC
boundary. It does not prove that their payload is concatenated into one Ethernet
packet. Disabling the common gate also resets progress counters, so subsequent
zero readback cannot be used as proof of a normal per-buffer completion.
SIPC RX was held off as an isolation measure; this experiment does not establish
every downstream pipeline stage's drain or isolation semantics.

Raw logs are `out/acceptance/sg-dma/probe-without-common-enable.txt` and
`out/acceptance/sg-dma/probe-common-enable.txt`. Each experiment ended with WOE
disabled, followed by a full reboot, TFTP reload of the same 10,996,596-byte FIT,
and `bootm`. No environment was saved and no flash data was written.

### Additional factory and public-source recovery

The original `vendor-reference/2b5/rootfs.jffs2` was parsed offline with JFFS2
header, directory, inode and payload CRC checks. It contains 993 directory entries,
no `.ko` files, an empty `/wlan`, and only `mcode_intel.bin` in the PPU firmware
directory. Reconstructing that firmware from its 27 inode versions yields SHA-256
`056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40`, matching the
current firmware. Thus scanning the base rootfs does not supply the missing WOE
client or alternate MTK PPU image.

The board's separate Plugin partition was also read through `/dev/mtd6ro`, without
attaching or mounting it. All 41,943,040 bytes transferred, and the host hash
matched a second board-side read:
`0a1cfdd3603d80ba77de46356b283b0e5ce3d9ad677aed44fed6e92bea3a9a41`.
Each full read reported zero uncorrectable ECC errors and three corrected bits.
The recoverable directory inventory contained no WOE/WLAN/module paths. Some old
JFFS2 records failed CRC validation and were excluded; this is not a claim of
complete filesystem recovery. Unrelated plugin file contents were not inspected.

A new primary source is the Intel driver published by prpl Foundation,
`iwlwav-dev` commit `98fc4a2ff5b7df10054e9f1645df621ebde8f848`:

- [mhi_descriptors.h](https://gitlab.com/prpl-foundation/intel/iwlwav-dev/-/blob/98fc4a2ff5b7df10054e9f1645df621ebde8f848/drivers/net/wireless/intel/iwlwav/wireless/shared_mbss_mac/mhi_descriptors.h#L11)
  defines a five-word host descriptor, with payload address in word 2, control in
  word 3, and buffer index in word 4.
- [mhi_umi.h](https://gitlab.com/prpl-foundation/intel/iwlwav-dev/-/blob/98fc4a2ff5b7df10054e9f1645df621ebde8f848/drivers/net/wireless/intel/iwlwav/wireless/shared_mbss_mac/mhi_umi.h#L1825)
  defines word 3 length bits 15:0, byte-offset bits 25:23, SOP/EOP bits 29:28, and
  ownership bit 31. The field width is three offset bits despite an adjacent
  comment saying two.
- [hw_mmb.c](https://gitlab.com/prpl-foundation/intel/iwlwav-dev/-/blob/98fc4a2ff5b7df10054e9f1645df621ebde8f848/drivers/net/wireless/intel/iwlwav/wireless/driver/hw_mmb.c#L2202)
  uses first/last/middle values 2/1/0 for SOP/EOP, reads the words as little-endian,
  and joins multi-buffer RX packets by a software copy in its ordinary receive
  path. This is a concrete descriptor candidate for the factory WAV600 mode;
  the public driver has no ZTE WOE integration and does not prove the ZTE PPU's
  payload assembly behavior.

Factory `woeRxSetupWAV600 @ 0x5adc` sets DMAD mode 0, DMAD bytes 0, RXD size from
config, and disables RXD/DMAD swap. Its descriptor stride and the precise record
consumed by this board's `mcode_intel.bin` still require ZTE-side confirmation.

The release investigation also recovered plat
`net_check_reorder_rls_nolock @ 0xb050`: a separate release ring provides a base,
depth, consumer index and completed count to registered callbacks, then
acknowledges the count through `idm_rls_update`. No callback registrant was found
in the collected 49 module paths. NP `idm_woe_set_idm_tx_ser_enable @ 0x14094`
additionally drains outstanding buffer addresses through a reset-time callback.
These reinforce the need to configure and validate the release path before
allowing synthetic payloads into normal PPU/IDM processing.

### Final recovery and checks

After the second experiment, the original FIT was reloaded and the observed
WAN/LAN addresses restored. The probe module is absent. SIPC configuration is
`0x3`, WOE common/RX control is `0`, and WOE ring enables are `0`. RX checksum is
on and SG remains off. The serial console log level was restored to 7.

| Recovery test | Result |
| --- | --- |
| WAN, 8 TCP streams | 16,777,216 bytes verified, zero bad bytes |
| LAN, 8 TCP streams | 16,777,216 bytes verified, zero bad bytes |
| TX errors / timeouts | Zero in both runs |
| Pending TX / RX refill deficit | Zero |
| Kernel BUG / Oops / WARNING / watchdog / DMA-error scan | No matches |

Evidence: `out/acceptance/sg-dma/recovery/mq-{wan,lan}-result.json`, corresponding
board/peer logs, and `out/acceptance/sg-dma/final-recovery.txt`. The probe built
with `W=1`, and checkpatch reported zero errors and warnings. Both repositories'
Git whitespace checks passed. Generated Kbuild files were cleaned from the source
directory; the temporary Plugin dump was removed after its metadata inspection.

## WAV600 payload probe and ISU initialization correction

`port/mainline/sg-acceptance/woe-wav600-probe.c` submits a 128-byte Ethernet /
IPv4 / UDP frame with valid checksums and deterministic nonzero payload. Each
20-byte Intel HOST_DSC points to a separate 4 KiB range in a retained coherent
allocation. A second frame is split into 64 + 64 bytes with FIRST/LAST flags;
it is submitted only if the complete frame first reaches the Linux packet hook
and compares exactly. Intel `MTLK_OWNER_DRV` is **1** (`OWNER_FW` is 0), so the
input control word uses bit 31 plus SOP/EOP bits 29:28 and length bits 15:0.

The probe uses factory WAV600 WOE settings: mode 0, zero separate DMAD bytes,
20 RXD bytes, intercept length 20, RXD/DMAD swap disabled. WOE RX and common
enable are set, while WOE TX, peer polling and WOE interrupts remain disabled.
It records the three already-configured IDM release rings without acknowledging
or freeing entries. All submitted allocations remain retained until a RAM reboot.

Initial complete-frame trials, including both settings of the unknown-station
flag, advanced WOE RD, WOE/SIPC counters and the RED NP1 ring counter. However,
RED NP1 forwarded counts and all eight PPU ME counters remained unchanged.
The ring counter alone therefore did **not** establish PPU execution. Evidence:
`out/acceptance/sg-dma/wav600-{initial,np1-trace,known-station,all-engines}.txt`.

The cause of that first stall was an initialization omission. In factory
`np.ko`, `isu_init @ 0x3ee48` calls `isu_set_dwrr_weight @ 0x3e1ec` with **four**
arguments `(2, 2, 1, 1)`. Disassembly at `0x3eec0..0x3eed0` sets `w3=1`, `w1=2`,
`w2=w3`, `w0=w1` before the call. The earlier decompilation displayed only three
arguments. The callee packs PON, ETH, protocol and new-input weights into the
four successive bytes of ISU register `0x19010040`. Mainline had initialized
`0x00010202`, leaving the fourth weight zero. It now writes **`0x01010202`**.
This corrects basic input dispatch; it does not add QoS policy or shaping.

The controlled `admit_np1=1` test submitted exactly one descriptor under the old
weight and then changed only the fourth byte, without another submission:

| Observation | Before admission | After admission |
| --- | ---: | ---: |
| WOE read pointer | 1 | 1 |
| RED NP1 input/output forwarded | 0 / 0 | 1 / 1 |
| RED NP1 input/output dropped | 0 / 0 | 0 / 0 |
| PPU MEC descriptors | 2680 | 2681 |
| ME0 MF valid input/output | 2680 / 2680 | 2688 / 2688 |
| IDM packets from SMCT | 0 | 1 |
| IDM NP1 to NP2 | 0 | 0 |
| IDM release counts, all three rings | 0 / 0 / 0 | 0 / 0 / 0 |

Raw capture: `out/acceptance/sg-dma/wav600-isu-admit-ab.txt`. Booting the corrected
FIT reproduced forward progress without the probe changing ISU. Its SHA-256 is
`65b0fa1174e37603d770b075446b9631ca367daa39a1e3af6812ea3894678d29`, size
10,997,052 bytes. See `wav600-idm-dma.txt` in the same evidence directory.

The next stage is now identified: the packet reaches **Wi-Fi trap queue 16**,
outside the production Ethernet RX polling set (queues 0..15). Factory
`plat_132.ko` `idm_net_poll @ 0xc294` reads packed queue 16/17 counts through
`idm_get_cpu_rx_cnt(8)` at IDM offset `0xe4`; `idm_net_rx @ 0xbf6c` consumes
queue 16 or 17. `idm_set_wifi_trap_info @ 0xbd8c` classifies reasons `0x62/0x63`
as NP1 diagnostics.

On a fresh corrected FIT, both `unknown_sid=0` and `unknown_sid=1` yield one
queue-16 record. Its length word is `0x00310020` and first metadata word is
`0x00001062`: reason/detail **`0x62/0x10`**, 32 diagnostic bytes. Those bytes are
the five submitted HOST_DSC words, represented in big-endian word order and
padded with zeros, rather than the Ethernet payload. This is a real exception
path, not evidence of successful payload DMA, SG gathering or final ownership
return. The exact meaning of detail `0x10` is not recovered. No split frame was
submitted because the complete-frame gate failed. Evidence:
`out/acceptance/sg-dma/wav600-unknown-station-trap.txt` includes both SID cases
and read-only descriptor/payload captures. An attempted `ioremap_wc` of the
coherent RX descriptor allocation returned NULL; that ineffective diagnostic
code was removed. The pending-count trace remains in the final module.

An outstanding configuration lead is factory `tm_wifi_global_set @ 0x37150`,
which writes two words to SDT100 entry 0, and `tm_wifi_ssid_set @ 0x37210`, which
writes one word to SDT104. Their field meanings and actual client initialization
values have not been recovered. No collected module other than `np.ko` contains
`tm_wifi_global_set`, and `kernel-2b5.elf` contains no such symbol. This is a lead
to investigate, **not** a proven cause of detail `0x10`; do not guess and write
global Wi-Fi table bits or enable REORDER with uninitialized DDR buffers.

After the final RAM reboot, the corrected FIT passed WAN and LAN recovery:
eight streams and 16 MiB per side, byte-for-byte, with both TX queues used and
drained. TX errors/timeouts, RX errors and refill deficit were zero. Probe
modules were absent, WOE disabled and queue 16/17 pending counts zero. Evidence
is in `out/acceptance/sg-dma/recovery-wav600/`; older recovery captures remain
in their separate directory. Full build, driver/probe `W=1`, strict checkpatch
and both repositories' whitespace checks passed. Flash was not modified.

## NP1 trap instruction and Wi-Fi table readback (2026-09-06)

The continuation retained the corrected production FIT above and changed only
the diagnostic fixture and transient RAM state. SG remains unimplemented.

The fixture now reads hardware SDT definitions with the existing SMMU0 command
contract (`0x28000000` for SDT read, `0x0c000000` for 128-bit ERAM read). Data
word order is calibrated against SDT0, whose definition is
`00004000/16030000`. It does not write Wi-Fi table contents.

| Table | Hardware definition words | First ERAM block |
| --- | --- | --- |
| SDT100, global Wi-Fi | `00000004 / 1406af20` | four zero words |
| SDT101, BSSID | `00000084 / 1606af30` | four zero words |
| SDT104, SSID | `00000040 / 1206d7c0` | four zero words |

These definitions already existed in `zx279133_core_sdt_info`; they were not
missing from mainline. Zero content does not identify which fields should be
set or prove that these tables cause detail `0x10`. Factory
`tm_wifi_staid_set @ 0x37330` accepts a 6-byte key and 16-byte response for
SDT110; `tm_wifi_staid_v2_set @ 0x37654` accepts a 4-byte key and 8-byte response
for SDT107. These wrappers provide sizes, not the required field semantics.
Evidence: `out/acceptance/sg-dma/wav600-wifi-table-readback.txt`.

An instruction-RAM marker established the source of the diagnostic detail:

- Factory PC5217 is `01047000:20000526` (high:low).
- `trap_marker=1` changes only the low word to `22000526`.
- Before writing, all four instructions PC5216..5219 must match the factory
  image. After writing, the complete block is read back and checked.
- The returned queue-16 metadata changes from `00001062` to **`00001162`**.
  Reason `0x62` and diagnostic length 32 remain unchanged.

This proves that PC5217 supplies detail `0x10` for this test. It does not prove
that the original microcode is wrong or reveal its preceding validation rules.
Only instruction RAM was changed; the firmware file and FIT were unchanged.
The next RAM boot reloaded the original instructions. Evidence:
`out/acceptance/sg-dma/wav600-trap-marker.txt`.

Further register experiments did not produce a validated stopped context:

- Idle SPR/RSP reads showed no changes even when a PPU ME processed the test.
- A candidate self-branch at PC5217 led to one RED NP1 output drop when sampled
  after 200 ms. The original instruction block was then restored and checked.
- Immediate sampling observed an active ME and restored the original block
  before normal exception delivery. Sampled SPR/RSP words did not establish
  correspondence with the input descriptor. The breakpoint-address register
  is not a validated current-PC register in this experiment.
- Enabling factory debug mode just during that sample returned debug-valid 0;
  debug mode was restored to 0. The instruction limit read `0x2fff` and was
  not changed. No valid hardware breakpoint protocol was established.

The unsuccessful sampling/self-branch code was removed from the maintained
fixture. Raw `wav600-{ppu-registers,hold-at-trap,immediate-registers,debug-registers}.txt`
and historical `debug-probe-source.c` remain under `out/acceptance/sg-dma/`.
Their register values must not be treated as decoded payload or DMA state.

The maintained `variants=1` mode submits four complete descriptors, each with
a separate coherent payload range. Each differs from the SID0 baseline only
in the listed metadata, apart from its required buffer address/BD index:

| Case | info0 | info1 | control | Measured result |
| --- | --- | --- | --- | --- |
| SID0 | `00000000` | `00000000` | `b0000080` | queue16 `0x62/0x10`, 32 bytes |
| SID128 | `00000080` | `00000000` | `b0000080` | same |
| EP1 | `00000000` | `00000100` | `b0000080` | same |
| OWNER_FW | `00000000` | `00000000` | `30000080` | same |

WOE RD, SIPC, NP1 forwarding and the Wi-Fi trap count each advance to 4. All
four diagnostic records contain their corresponding input descriptor. No
complete Ethernet frame matched, and no IDM release count advanced. This
rules out these specific single-field changes as a fix, not every possible
descriptor or station configuration. `variants` cannot be combined with the
split-frame or instruction-marker experiment. Evidence:
`out/acceptance/sg-dma/wav600-descriptor-variants.txt`.

The remaining blocker is the valid NP1 input/configuration and terminal DMA
ownership contract. A diagnostic trap, a consumed descriptor or a recovered
error-code literal is insufficient to advertise `NETIF_F_SG`.

After the final RAM reload, WAN and LAN each passed an eight-stream 16 MiB
byte-for-byte recovery test with zero bad bytes. Both TX queues drained;
RX/TX errors, TX timeouts and RX refill deficit were zero. Probe modules
were absent, WOE and PPU debug mode disabled, Wi-Fi trap counts zero, all XPS
masks restored, and the kernel fault scan empty. Evidence is separately
stored in `out/acceptance/sg-dma/recovery-np1-trace/`. The maintained module
built with `W=1`, passed strict checkpatch, and its Kbuild intermediates were
cleaned. No production-kernel change or flash write was made in this phase.

## NP1 branch control and context capture (2026-09-06)

A positive control now verifies an early diagnostic branch. It changes only
PC5177's low 15 condition bits, preserving its existing PC5222 target. The
entire four-instruction block is checked before and after each RAM-only patch.
Each row below used a fresh boot and one complete 128-byte Ethernet frame:

| Marker PC | Original high:low | Patched high:low | Queue-16 metadata |
| --- | --- | --- | --- |
| 5177 | `01080a1d:0a333a00` | `01080a1d:0a330520` | `00000762` |
| 5180 | `0290a253:80048401` | `0290a333:80048401` | `00001062` |
| 5181 | `0290a25f:40042461` | `0290a337:40042461` | `00001062` |

PC5177's result supports the tested `0x0108` branch condition and the PC5222
detail-7 exit. PC5180/5181 use a different `0x0290` instruction family; their
candidate target-field changes did not affect the result. This does **not**
prove either branch was untaken or that this family's encoding is correct.
All three tests still produced only a 32-byte diagnostic record, with zero
matched Ethernet frames and zero IDM release completions. Evidence:
`out/acceptance/sg-dma/wav600-branch{5177,5180,5181}.txt`.

Two additional context experiments were inconclusive and their code was removed:

- Factory `ppu_cluster_set_mcode_spr_clr_en @ 0x7ae10` controls bit 0 at
  `0x180904a4`. Temporarily clearing it around one frame, then restoring it to
  1, yielded nonzero SPR/RSP words without a validated match to the input.
- All 24 explicit key-read requests (IKEY/EKEY/STAT for eight threads of the
  ME whose MF counters advanced) completed. Some EKEY words resembled an IPv4
  flow, but no before/after thread comparison identified the submitted frame.
  These reads do not establish the frame's table lookups or DMA ownership.

Captures are `wav600-retain-spr.txt` and `wav600-thread-key-snapshot.txt`;
historical source is `retained-context-probe-source.c` in the same evidence
directory. Debug register values remain research data, not decoded live state.

The factory `tm_wifi_port_init @ 0xf1f8c` loop writes its caller-supplied value
to attribute **71** for ports 16 through 48. Disassembly and
`spa_port_attr_table` establish that 71 is an attribute ID mapping to the whole
MF word 19, not an enable value. `zte_api_wifi_init @ 0xf68c8` passes its incoming
argument through unchanged. The argument's meaning and actual initialization
value remain unknown; no port attribute was changed based on this lead.

SG remains unimplemented. The detail-7 control improves diagnostic coverage;
it does not supply a payload-forwarding or final-buffer-release contract.
The production firmware file, FIT and network driver were unchanged in this
phase; each instruction patch was discarded by reloading the original RAM FIT.

Recovery on that FIT passed eight streams and 16 MiB per side for WAN and LAN,
with zero bad bytes. WAN queue completions advanced by 694/11,297 and LAN by
496/11,186; both queues drained. Final RX/TX errors, TX timeouts and refill
deficit were zero. The probe was absent, WOE/debug mode disabled, queue 16/17
empty, SPR clearing restored to 1, all four XPS masks restored to 0, and console
loglevel restored to 7. The fault scan had no faults. Evidence is in the new
`out/acceptance/sg-dma/recovery-np1-branches/` directory. The maintained probe
built with `W=1`, passed strict checkpatch, and its build intermediates were
cleaned. Outer and nested Git whitespace checks passed. No flash was written.

## Factory runtime baseline and SPA comparison (2026-09-06)

The user supplied the factory boot sequence `cspboot; cspstart`. It successfully
booted factory Linux 5.4.196. The runtime had the wired `np`, `plat_132` and switch
modules loaded, no Wi-Fi NIC module, and no wireless interface. WOE control
`0x19204000` read 0, ring-0 configuration `0x19205008` read `0x4041`, depth
`0x19205194` read `0xfff`, and base `0x192052a8` read `0xffffffff`.
This is a real inactive WOE baseline, not an operating Wi-Fi SG reference.

Factory sysfs getters provided hardware reads through the driver's own access
functions. These getter files accept read requests via sysfs writes:

```sh
echo '16 71' > /sys/devices/platform/np/spa_debug/port_attr_table_get
echo '100 0' > /sys/devices/platform/np/se_debug/eram_table_get
echo '1438' > /sys/devices/platform/np/ppu_debug/inst_get
```

Port/table arguments are decimal; `inst_get` takes a hexadecimal PC and returns
four instructions. Results appear in `dmesg`. Factory serial input treats the
literal caret character as a kernel-debug trigger, so avoid it in shell commands.
Kernel console output also interleaved with a serial marker during a help read;
temporarily setting RAM console loglevel to 1 resolved the capture problem.

The actual attribute-71 values (SPA word 19) were:

| Ports | Factory value | Mainline RAM value |
| --- | --- | --- |
| 0, 14 | `00060000` | same |
| 5, 10 | `00042000` | same |
| 6 | `148e0000` | same |
| 15 | `00040000` | same |
| 16–19, 27–30 | `148c0800` | `00000000` |
| Remaining ports 20–48 | `148c0000` | `00000000` |

Sampled factory start-PC attributes for ports 16, 17, 31, 32 and 48 all read
`0x1269`, matching mainline word-0 decoding. Factory SDT100/101/104 entry 0 each
returned four zero words, matching the previous mainline reads. Sixteen sampled
instruction words at PCs 0–3, 4712–4715, 5176–5179 and 5216–5219 matched
`out/mcode_intel.bin`; this is a sampled match, not a whole-image hash comparison.

On a fresh mainline RAM boot, `spa-port-read.sh` captured words 0 and 19.
`spa-wifi-attrs-ab.sh apply` then copied only the 33 factory Wi-Fi word-19 values,
with every write verified, before running the unchanged complete-frame/split-gate
probe. WOE, SIPC and NP1 each advanced once; queue 16 still returned
`00310020 / 00001062`, with zero matched Ethernet frames and zero releases.
The split frame was not submitted. This excludes those SPA word-19 changes
alone as a fix; other table copies and field semantics remain unverified.
No production initialization was changed based on this experiment.

**Factory startup was not flash-read-only.** After login, its own `upgrade_mgr`
launched `/var/syn_version`. The console recorded erasing the low firmware area
at `0x600000`, length `0x2900000`, then copying from `0x2f00000` to `0x600000`.
It printed `syn_version over!`, and the process manager reported
`exit_code = 0 exit_status = 1`; a subsequent process listing showed no running
`syn_version`. It was allowed to finish before rebooting. No explicit erase,
flash-programming or `saveenv` command was issued by the collector, but the
complete factory visit cannot be described as leaving flash untouched. Future
captures must account for this automatic synchronization before starting the
full factory service stack. This observation supersedes assumptions that a
read-only root filesystem makes the entire factory startup read-only.

Evidence: `out/acceptance/sg-dma/factory-20260906/`, including inventory,
WOE/table/instruction reads, port comparisons, the RAM A/B, and the automatic
sync log. The final RAM reload discards all temporary SPA and probe state.

Final recovery on the original FIT passed WAN and LAN eight-stream, 16 MiB
integrity tests with zero bad bytes. Queue completions advanced by 683/11,304
on WAN and 489/11,182 on LAN. Both queues drained; RX/TX errors, TX timeouts
and refill deficit were zero. All 33 temporary attributes read back as zero,
the probe was absent, WOE/debug/trap state was zero, SPR clearing was 1, XPS
masks were 0, and console loglevel was restored to 7. The fault scan contained
only normal watchdog initialization. Evidence:
`out/acceptance/sg-dma/recovery-factory-baseline/`. Both new scripts passed
`sh -n` and their hardware read/write/readback paths were exercised. Outer and
nested Git whitespace checks passed; no production-kernel rebuild was needed.

## NP1 predicate experiments (2026-09-06, ongoing)

The path fixture now reads Wi-Fi trap records through `eth->rx_descs`, using
`netdev_priv(eth0)` from the exact running mainline build. The DMA address is
checked against IDM's descriptor-base register, and `dma_rmb()` precedes reads.
Queue 16 is not polled by this Ethernet driver; the probe never acknowledges
these records. Every submitted input buffer remains pinned until RAM reboot.

Temporary unconditional jumps to the established PC5222/detail-7 exit changed
the result at PCs **5177, 5178, 5180, 5181 and 5217**. The same diagnostic jump
at sampled PCs 5182, 5183, 5186, 5187, 5188, 5194, 5195, 5204, 5209, 5214 and
5216 left the original detail `0x10`. All modified blocks were restored and
read back successfully. This narrows the observed transfer to the vicinity of
PC5181; it is not a general-purpose instruction trace or complete ISA decoder.
Capture: `out/acceptance/sg-dma/wav600-path-trace.txt`.

Changing PC5181 from `0290a25f:40042461` to `0290a25f:4004c461` redirected the
result from `0x62/0x10` to `0x62/0x07`. This increments a candidate displacement
by 5 and agrees with the five-instruction distance between the two detail
literals. The analogous PC5180 change had no effect. Earlier edits to the
high-word candidate target field did not establish a branch encoding.

The field probe preserves this redirected taken exit and replaces PC5182
with a jump to the original detail-16 exit. Both outcomes therefore remain
diagnostic. It varies only comparison bits 5..9 at PC5181:

| RXD swap | Comparison 0 | Comparison 1 | Comparison 2 | Comparison 3 |
| --- | --- | --- | --- | --- |
| 0 | detail 16 | detail 7 | detail 7 | detail 7 |
| 1 | detail 7 | detail 16 | detail 7 | detail 7 |

Both rows use a complete descriptor, unknown-SID clear, and control word
`b0000080`. The results agree with a not-equal comparison of a two-bit field
whose value changes from 0 to 1 with RXD swapping. A further 32 single-bit
comparison experiment reconstructed the candidate source as **`b0000080`**
with RXD swap enabled, exactly matching the submitted control word. This
supports the measured field starting at bit 29: `(0xb0000080 >> 29) & 3 == 1`.
The public Intel header instead places SOP/EOP at bits 28..29. The fixture
therefore exposes an explicit `sop_shift=29` comparison while retaining the
old default; payload delivery and SG remain separate acceptance requirements.

Captures: `wav600-relative-trace.txt`, `wav600-rxd-swap-trace.txt`,
`wav600-field-probe.txt`, and `wav600-register-bit-probe.txt` under the same
evidence directory. At this stage there is still no successful payload or
release-completion claim, and the production driver is unchanged.

## Complete payload delivery and rejected split (2026-09-06 continuation)

The continuation ran here after the original task stopped. Changes are confined
to the standalone probe and evidence documentation. The production driver,
on-disk microcode and RAM FIT were not changed during this continuation.

### Effective complete-frame configuration

The successful experiment uses all of the following on a fresh RAM boot:

- `rxd_swap=1`, `sop_shift=29`, `unknown_sid=0`; a 128-byte complete descriptor
  has control word `e0000080`.
- Temporary SDT104 entry 0 value **`80000010`**. The existing definition is
  `00000040/1206d7c0`, matching the factory's 64 entries of 32 bits. The probe
  preserves the other three words of the containing ERAM block, checks each
  write by reading it back, and restores entry 0 to its initial zero value.
- Factory-captured SPA word 19 for Wi-Fi logical ports 16–48, applied by
  `spa-wifi-attrs-ab.sh`. This helper changes RAM; the final reboot clears it.

These settings now have staged evidence. With the corrected control word and
SDT104 still zero, the diagnostic becomes `0x62/0x00`. The path reaches PC5187,
and an immediate hardware snapshot records i-key request `00060468`, index 0:
the low table byte is `0x68`, SDT104. Single-bit comparisons of entry 0 leave
that diagnostic unchanged for bits 0–30. Bit 31 changes the path to NP2.
Value `80000000` leaves the following NP1 input blocked after the first frame.
Value `80000010` changes the SIPC state; adding the factory SPA attributes
then produces a complete frame on queue 16. This is an observed working
combination, not a complete specification of the SSID bit fields.

The first successful RX descriptor was:

```
address       83be9100
length_flags  07900080
metadata[0]   00000765
metadata[1]   c0a80101
```

It contains 128 bytes, source-port field 16, and NP2 diagnostic `0x65/0x07`.
The entire Ethernet/IPv4/UDP frame, including the seeded payload, matches the
submitted buffer. An independent comparison of the raw readback found zero
different bytes; IPv4 and UDP checksums both validate. Earlier `matched=0`
from the packet callback was an incomplete observation: the normal Ethernet
driver does not poll queue 16. It did not mean that this complete payload had
not arrived.

The fixture now reads these records through the running driver's descriptor
and page mappings. It pauses NAPI while looking up/copying the retained page,
synchronizes DMA before reading, and compares the full frame. This initial
experiment does not acknowledge queue 16. The later optional ACK experiment
below consumes inspected queue records without recycling any DMA buffers.
Neither experiment frees source buffers or copies fragments together.

### Descriptor field calibration

`field_probe=3` first sends a diagnostic baseline before enabling any normal
payload submission. It then samples source-register bits before descriptor
serialization, restoring and checking each modified block. With a fixed
payload DMA address, the PC5198 results are:

| Source | Reconstructed value |
| --- | --- |
| 11, submitted control | `e0000080` |
| 17 | `00000000` |
| 18, submitted DMA address | `9bf01000` |
| 19 | `00020000` |
| 20 | `00000000` |

The control and address agree with the known inputs. `00020000` equals the
128-byte length shifted left by 10. Sampling source 19 at PC5194, immediately
after its PC5193 write, had returned zero; that earlier value must not be treated as
the final descriptor length. This experiment does not fully decode the ISA:
the alternate diagnostic outcome at these sampling sites has metadata zero
and length/flags `20000020`, calibrated against the known control and address.

The raw IDM length counters also need correct interpretation. Factory
`idm_stat()` labels the high 16 bits of `0x224`/`0x228` as errors and the low
16 bits as correct lengths. Thus `0x228=1` is a successful length count, not
an error. SIPC's NP2 input showed one packet and one burst with zero length
errors and zero short-packet drops in the complete-frame experiment.

### Repeat and actual scatter test

On another fresh boot with the working combination above, `split=1 repeat=3`
verified three complete frames byte-for-byte on queue 16. It then submitted
two 64-byte ranges from different DMA pages, with first/last flags 2 and 1
at the measured flag position. Both descriptors were consumed, but each
returned a separate 32-byte `0x62/0x10` diagnostic. No 128-byte second frame
was delivered: **`matched=3/0 bad=0`**. NP2 count remained 3 after the fragments.

This is direct evidence that the current Intel NP1 entrance rejects the tested
segmented descriptors. It is not evidence that the SoC has no gather engine.
The next missing contract is how to represent one multi-buffer packet for a
hardware path that accepts it, followed by final DMA ownership/release and
ordinary Ethernet TX integration. `NETIF_F_SG` remains disabled. All three
release counts stayed zero, with unchanged sentinel entries; source buffers
and queue-16 pages stayed pinned until reboot.

Evidence is under `out/acceptance/sg-dma/`: `takeover-key-path.txt`,
`takeover-first-trap-keys.txt`, `takeover-ssid-bits.txt`,
`takeover-descriptor-late-fields.txt`, `takeover-np2-pipeline.txt`,
`takeover-ssid-port16.txt`, `takeover-ssid-port16-factory-attrs.txt`,
`takeover-np2-trap-payload.txt`, `takeover-first-split.txt`, and
`takeover-repeat-split.txt`. The `takeover-ssid-enabled-path.txt` run is not
usable as a reachability trace after its first normal frame: later NP1 input
was blocked. Its missing queue records must not be interpreted as untaken PCs.

Recovery reloaded the original 10,997,052-byte RAM FIT and cleared all 33
temporary SPA attributes. WAN and LAN each passed eight streams / 16 MiB with
zero bad bytes; both hardware TX queues completed every submission. Queue
completion deltas were 946/11,066 on WAN and 443/11,242 on LAN. Results and
board/peer logs are in `out/acceptance/sg-dma/recovery-takeover/`. This
continuation did not boot factory Linux or write flash or saved U-Boot state.
Final readback confirms SDT104[0], WOE controls and queue-16 pending count are
zero, SPR clearing is 1, all four XPS masks are zero, and the probe is absent.
RX/TX errors, TX timeouts and RX refill deficit are zero, and the kernel fault
scan is empty. The final module build with `W=1`, strict checkpatch, and both
repositories' whitespace checks passed. An overlong final serial command lost
its trailing marker; the separate short commands in `final-recovery.txt`
completed successfully. The earlier timeout transcript is retained separately.

## Independent DMAD sections and queue ACK (2026-09-06 continuation)

This experiment separates WOE's last-section extraction from the Intel
SOP/EOP predicate. It retains the successful SSID/SPA configuration above and
keeps the Intel control flags at 3 for every input. The new `separate_rxd=1`
mode uses a 32-byte DMAD ring and independently allocated 32-byte RXD headers:

| DMAD word | Submitted field |
| --- | --- |
| 0–4 | Intel header prefix, including the actual payload address and length |
| 5 | DMA address of the separate RXD header |
| 6 bit 0 | WOE last section: 1 for each complete frame, then 0/1 for the fragments |
| 7 | Zero |

The RXD header contains the ordinary five Intel words with zero padding.
Each payload occupies a different DMA page; RXD headers occupy further pages
in the same retained coherent arena. No software gather or fragment copy occurs
after submission. The first/last fragment controls are both `e0000040`; their
independent DMAD last-section bits differ.

The effective WOE registers, relative to `19204000`, are:

| Offset | Value | Configuration |
| --- | --- | --- |
| `1008` | `00008041` | Descriptor mode 1, DMAD/RXD lengths both 32 |
| `100c` | `00002020` | Intercept both 32-byte records |
| `1010` | `00000003` | Independent DMAD and RXD word swapping enabled |
| `1044` | `80000006` | Last-section extraction from DMAD word 6 bit 0 |
| `1048` | `80000005` | RXD pointer extraction from DMAD word 5 bit 0 |

Without `dmad_align=1`, NP1's diagnostic copy shows the forwarded DMAD prefix
shifted right by one byte: the known payload address and control word no longer
match their expected fields. The fixture compensates by shifting the first
five DMAD words left by eight bits, carrying the next word's high byte. It
leaves words 5 and 6 intact. This is an empirical calibration of this exact
experimental format, with zero high byte in info0; it is not a universal DMAD
layout or a recovered explanation of the byte shift.

The final maintained-code run used `split=1 repeat=3 ack_traps=1` and produced:

- Three exact 128-byte complete frames on queue 16.
- Two independent, exact 64-byte payloads matching the first and second halves
  of the seeded second frame. Both have metadata[0] `00000765`.
- WOE `enter=5 eop=5 slices=1`, SIPC `5/5`, NP1 input/output 5 and NP2 count 5.
  IDM `0x228=5` counts five correct lengths; its error half is zero.
- **`matched=3/0 bad=0`**. The separate-fragment comparisons do not increment
  the assembled-frame counter. No gathered 128-byte second frame was received.

Thus the tested last-section control changes a WOE slice counter and still
produces two records at this observation point. The earlier flat-format
rejection and this per-fragment delivery are distinct results. Neither
establishes SG or, by itself, rules out downstream streaming within one frame.

### Queue consumption is not source-buffer completion

The optional `ack_traps=1` experiment runs only after inspecting the queue
records and verifying that the hardware pending count equals the inspected
count. It writes the packed queue/count to IDM `0x88`, after a DMA barrier.
It does not write the refill register at `0x100`, return RX pages to the pool,
unmap payloads, or free the coherent source arena. This follows the separation
between descriptor consumption and buffer refill in the existing driver;
factory `idm_rx_update @ 0x130c8` performs both register writes.

Five queue records were acknowledged, and pending fell from five to zero.
All three source-release counts remained zero; all sampled release entries
remained `55555555`. Queue metadata[1..5] was identical for the three complete
frames even though their submitted indices were 0, 1 and 2, so those samples
do not recover a source-index completion mapping. The consumed RX pages and
all source buffers remain pinned until the required RAM reboot.

### Reorder evidence and remaining boundary

Read-only binary analysis found `reorder_init @ 0x319b8`,
`reorder_ba_setup @ 0x2f924`, and `reorder_set_ba_ddr_base @ 0x2f4e0` in
`np.ko`. Initialization allocates per-BA address ranges spaced 372,736 bytes
across 64 entries, using a caller-supplied `woe_ddr_phy_start`. BA setup takes
BA ID, window, starting sequence number and AMSDU limits. The indirect RAM
interface and enable register are in the `19018000` block. These are wireless
reorder configuration interfaces; the available callers do not establish how
an ordinary skb's fragments should enter that engine or how their DMA
ownership returns. Reorder send/receive counters stayed zero throughout this
fixture. No reorder RAM or BA state was initialized in this continuation.

The available `net_check_reorder_rls_nolock @ 0xb050` in `plat_132.ko` polls
three release rings and passes their positions/counts to registered callbacks.
The available Wi-Fi-less runtime does not provide the corresponding producer
contract. BA windows and AMSDU limits alone are not evidence of arbitrary
payload scatter-gather.

Raw captures are in `out/acceptance/sg-dma/`:
`continue-separate-rxd-32-trap.txt` records the byte shift;
`continue-separate-rxd-aligned.txt` records the corrected single/split run;
`continue-separate-rxd-repeat-ack.txt` and `continue-separate-rxd-final.txt`
record repeated exact payload checks and ACK without source release.
The earlier DMAD-only and 20-byte separate-RXD attempts stopped at the
complete-frame gate; their captures are retained, and the unsuccessful
DMAD-only option was removed from the maintained fixture.

Recovery reloaded the original 10,997,052-byte RAM FIT. WAN and LAN each
passed eight streams / 16 MiB with zero bad bytes. TX queue completion deltas
were 1,019/10,971 on WAN and 822/10,875 on LAN, equal to all submissions.
`recovery-dmad-sections/` contains the board/peer captures and JSON results.
Final readback confirms SDT104[0], both WOE controls and queue-16 pending are
zero, all 33 temporary SPA word-19 values are zero, SPR clearing is 1, all
four XPS masks are zero and the probe is absent. RX/TX errors, pending TX,
timeouts and RX refill deficit are zero; the kernel fault scan is empty.
RX checksum remains on and SG remains off. Host en8 retained `192.168.1.100`
and its 2.5G link, with no en8 bridge membership or QEMU process during recovery.
One intermediate TFTP transfer exhausted retries; the subsequent complete
transfer was verified before booting. No factory Linux, flash write or saved
boot-environment change occurred in this continuation. The standalone module
build with `W=1` and strict checkpatch passed.

## Other paths checked

- `woe_set_hif_txd_buf_info_location_cfg @ 0x1c05c` configures 13 buffer pointer
  and length locations. Its callers `woeTxSetupMTK_TXDV2 @ 0x3770` and
  `woeTxSetupMTK_TXDV3 @ 0x3af8` build Wi-Fi output descriptors. Associated token
  recycling accepts up to 13 addresses in PAO mode. This is not a CPU IDM
  scatterlist submission API.
- `fpga_xedmac_read_reg` accesses a PON MAC window. `np_vir_addr_map @ 0xdc744`
  assigns it `pon_base + 0x200000`; its name does not establish another Ethernet
  SG DMA controller.
- Read-only WAN XMAC feature 1 through 4, MTL operation, DMA mode/system-bus and
  channel-0 base registers read zero. This supplies no working native DesignWare
  DMA alternative; zero readback alone is not proof that the block is absent.

## Remaining implementation gate

A usable path must establish all of the following before exposing SG:

1. The hardware's address and **payload length for each segment**, byte order,
   segment limit, and end-of-packet encoding.
2. How those segments reach the existing WAN/LAN Ethernet output, including PPU
   parsing and output-port metadata when using WOE.
3. Submission ownership, final payload-read completion, interrupt/counter
   semantics, and drain/reset behavior for all mapped pages.
4. A RAM-only packet test using physically noncontiguous pages and distinct
   nonzero data across fragment boundaries, with exact peer verification and
   descriptor reuse/error/reset checks.

The wire test must first pass a complete control through the same candidate
egress. It must preserve the original frame boundary at the peer; it need
not first assemble a contiguous buffer inside WOE, IDM or an intermediate queue.

Current obstacle is the end-to-end payload and ownership contract, not missing
calls to `dma_map_page()` or the absence of a vendor SDK. The table extractor now
also ignores zero-filled terminal entries, which occur in the SIPC table and
otherwise produce a false second ID 0 at the NPPT base address. The WOE table
still yields its original 444 fields. No production-driver SG feature is enabled.

The subsequent [IDM producer audit](SG_IDM_PRODUCER_AUDIT.md) traced the
vendor `idm_flag` field to word 6 bits 27:26. Known producers use values
2 and 3 for contiguous-buffer and GSO-template paths, respectively; no
multi-address continuation contract was recovered. Value 1 remains unknown.
This was a static audit only, with no new board acceptance result.

Subsequent [source ownership experiments](SG_SOURCE_OWNERSHIP_TESTS.md)
provide two independent RAM counterexamples to using WOE descriptor RD as
payload completion: after RD=1 and before NP1 handoff, changing 86 source
bytes changes all 86 subsequently received bytes. Source allocations remain
pinned; neither RD nor the debug-only CPU packet match is a production
reclamation contract.

The [ordinary IDM native chaining audit](SG_NATIVE_IDM_AUDIT.md) covers
the known TX descriptor reservation callers, low-level ops, queue setup and
owner reclamation. It found only single-address producers and independent
descriptor batching; no native SG submission contract or grounded chain-mode
experiment was identified. This does not establish silicon impossibility.
