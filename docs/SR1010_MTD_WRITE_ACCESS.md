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
