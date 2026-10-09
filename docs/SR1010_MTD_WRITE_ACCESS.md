# SR1010 MTD write access

Validated on 2026-09-13 with a RAM/TFTP boot. The four requested partitions
have `MTD_WRITEABLE` enabled; bootloader, tags and defcfg remain read-only.
Partition offsets and sizes are unchanged.

| Mainline device | Name | Offset | Size | Access | Content |
| --- | --- | --- | --- | --- | --- |
| mtd0 | bootloader | 0x00000000 | 1 MiB | RO | Bootloader |
| mtd1 | tags | 0x00100000 | 1 MiB | RO | Factory tags, including MAC addresses |
| mtd2 | usercfg | 0x00200000 | 2 MiB | RW | JFFS2 |
| mtd3 | defcfg | 0x00400000 | 2 MiB | RO | Factory default configuration |
| mtd4 | firmware-slot1 | 0x00600000 | 41 MiB | RW | Whole firmware slot, not a single filesystem |
| mtd5 | firmware-slot2 | 0x02f00000 | 41 MiB | RW | Whole firmware slot, not a single filesystem |
| mtd6 | Plugin | 0x05800000 | 40 MiB | RW | JFFS2, ending at 0x08000000 |

## Factory evidence

`vendor-reference/sr1010-vendor-runtime/system/proc/mounts` records:

```
/dev/mtdblock3 /usercfg jffs2 rw,relatime 0 0
/dev/mtdblock9 /Plugin jffs2 rw,relatime 0 0
```

The extracted factory `etc/init.norm` also mounts both with `-t jffs2`.
Factory device numbering differs because its partition map exposes a whole
flash device and overlapping firmware views. Mainline retains seven fixed,
non-overlapping partitions and does not expose a whole-flash MTD device.

## Changes

- The SR1010 DTS replaces the chip-wide `read-only` mode with
  `partitions-only`, retaining the fixed-partition-only registration path.
- Board overrides remove `read-only` only from usercfg, firmware-slot1,
  firmware-slot2 and Plugin. Other partitions retain their write masks.
- Normal SPI-NAND initialization can now configure the chip's volatile
  protection/ECC state. Per-partition MTD protection still applies.
- The diagnostic configuration enables JFFS2, NAND write buffering, summary
  support, and the standard zlib/rtime compression support.
- No automatic filesystem mount or firmware flashing action was added.

## Acceptance and limits

Build: `JOBS=8 ./port/mainline/build-zxdbg.sh`, log `out/mtd-rw-build.log`.
FIT: `out/sr1010-zxdbg.itb`, 11,076,424 bytes, SHA-256
`b98b01914a7a28506ddbcf51ae38a84991f99ca2113d4c18f1c760abf42f0514`.
Boot record: `out/mtd-rw-boot.json`.

`out/mtd-rw-acceptance.log` confirms flags `0x400` for precisely mtd2, mtd4,
mtd5 and mtd6, and `0x0` for mtd0, mtd1 and mtd3. Both filesystems mounted
successfully using `mount -t jffs2 -o ro mtd:<name> <temporary-directory>`
and were unmounted successfully afterward. The firmware slots were not mounted.

Plugin scan reported an invalid JFFS2 node CRC at partition-relative offset
`0x020327f8`, a subsequent magic warning at `0x020327fc`, and empty-flash
notices. Mount still succeeded. No repair was attempted, and this does not
constitute a full integrity check of every file in Plugin.

All seven complete partition data SHA-256 values match the pre-boot baseline
in `out/mtd-rw-before.log`. The comparison includes usercfg and Plugin after
the read-only mounts. No program/erase or filesystem write test was issued;
write permission is verified, but successful writes and persistence are not
claimed as tested in this run.

The SPI-NAND DT schema check and relevant `git diff --check` passed. WAN ping
passed 3/3 (`out/mtd-rw-ping.log`), kernel taint is zero, and both temporary
JFFS2 mounts have been removed. Generated images and runtime logs are not
included in the source commits; no remote push was requested.

## Update 2026-10-09: JFFS2 writes on the Plugin partition

The first real writes on mainline, from OpenWrt on the device, showed that
JFFS2 cannot be used on this NAND as it is. After the partition was erased,
mounted, written with 300 KB, synced and unmounted, the next mount stopped at
the first block that held data:

```
jffs2: cannot read OOB for EB at 02740000, requested 8 bytes, read 8 bytes, error -74
```

-74 is -EBADMSG, an uncorrectable ECC error, and `ecc_failures` of the mtd
device counts it. JFFS2 writes the clean marker of an erased block into the OOB
of the first page and later writes the first node into the same page; the
on-die ECC of the W25N01KV (ID ef ae 21) is generated again at each program of
a page, which is the likely reason that such a page cannot be read back. This
is an inference from the symptom, not from a datasheet.

Kernel commit `0c3602121` adds `mtd_info.program_once`, set by SPI-NAND with
on-die ECC, and makes JFFS2 keep a page-sized clean marker in the data for such
a device, so no page is programmed twice. With it the same test passes and the
overlay in the Plugin partition keeps the settings over a reboot. Details,
checks and the effect on the vendor firmware, which shares the partition:
`target/linux/zte/docs/jffs2-on-spi-nand.md` in the ImmortalWrt tree.

Not checked: power loss during writes, long runs, and the vendor firmware
reading a Plugin partition written this way.

## Update 2026-10-09: SPI flash speed and JFFS2 mount time

The SPI flash controller now runs at 50 MHz (a 100 MHz parent and a divider; it
was 25 MHz) and the SPI NAND is read with four data lines: `spi-rx-bus-width =
<4>`, which selects the read from cache `6Bh`; commands, addresses and writes
stay on one line. Reading 40 MiB takes 3.9 s, about 10 MB/s. The read-back was
checked against the root file system of the running image and against the
read-only partitions read at 25 MHz, and no ECC errors were reported. The CPU
reads the controller FIFO, about 0.24 us per 32-bit word, which limits reads to
roughly 16 MB/s whatever the clock. The vendor kernel uses DMA for this
controller; mainline has no driver for that DMA controller, and the vendor
device tree gives the controller no `dmas`.

Mounting the 40 MiB Plugin partition took 16 s once its empty blocks carried a
clean marker as long as a page, because the scan then read each empty block to
its end. The scan now reads 1 KB after the marker and takes 0.24 s.

Details: `target/linux/zte/docs/spi-nand-speed.md` and
`target/linux/zte/docs/jffs2-on-spi-nand.md` in the ImmortalWrt tree.

## Update 2026-10-09 (later): the vendor kernel refuses the JFFS2 that OpenWrt wrote

After OpenWrt had used the Plugin partition, the vendor firmware (5.4.196) could not mount
it: `jffs2: Incompatible feature node (0xe008) found at offset 0x01642070`, then an I/O
error. 0xe008 is a JFFS2 xattr node; overlayfs writes extended attributes when the upper
file system supports them. The vendor JFFS2 has neither xattr nor summary support. The
ImmortalWrt target now builds JFFS2 without both. The partition has to be erased once
after upgrading from an earlier build. Mounting a partition written by the new build from
the vendor firmware, and the way back, have not been tried yet.
