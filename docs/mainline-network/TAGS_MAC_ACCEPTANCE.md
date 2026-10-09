# SR1010 factory MAC from tags

Date: 2026-09-13

> **Superseded for the LAN address on 2026-10-09.** The LAN conduit no longer
> reads tag `0x0101`; it keeps the WAN address, and the switch ports and the
> bridge inherit that. See [Policy change](#policy-change-one-address-for-wan-and-lan).
> The split-address results below are historical.

## Result

The mainline WAN driver obtains its MAC automatically from tag `0x0100`
through an NVMEM layout. Per the user's explicit assignment, the LAN conduit
reads tag `0x0101`, and the four DSA user ports inherit the LAN address.
The split-address RAM boot passed all six interface comparisons:

| Interface | Tag | Current MAC |
| --- | --- | --- |
| WAN `eth0` | `0x0100` | `7c:7d:21:b9:c1:3a` |
| `lan-cpu0`, `lan1` through `lan4` | `0x0101` | `7c:7d:21:b9:c1:3b` |

This implements read-only factory MAC configuration. It does not modify tags,
enable flash writes, or implement a tags editing interface.

## Factory evidence and LAN interpretation

The supplied `mod_tags.py` describes the actual format: a 12-byte little-endian
header containing magic `0x33333333`, payload length, and IEEE CRC32, followed
by variable-length key/value records with 16-byte record headers. Keys and
values are not padded to an alignment boundary.

The live tags partition has a 688-byte payload with a valid CRC:

| Tag ID | Value | Meaning supported by evidence |
| --- | --- | --- |
| `0x0100` | `7c:7d:21:b9:c1:3a` | Base MAC used by factory Ethernet interfaces |
| `0x0101` | `7c:7d:21:b9:c1:3b` | Second MAC in the factory allocation table |

`vendor-reference/sr1010-vendor-runtime/network/ip-address.txt:23-40` records
factory WAN `eth0`, LAN `eth1` through `eth4`, `br0`, and `nbif0` all using the
first address. There is no evidence that the four LAN jacks each have a
separate factory MAC. Physical switch ports do not require distinct addresses
when used as members of the same LAN bridge.

Static vendor evidence:

- `vendor-reference/2b5/cspd`, `dbPersonInitMACProduct`: scans tag IDs
  `0x0100..0x01ff`, accepts six-byte values and populates the first two
  `IGD.MAC1`/`IGD.MAC2` records.
- The same binary, `EthGetMACForLan` at `0xa8bb4`: requests the LAN MAC through
  `CfGetMacByType(4, ...)`, with a product-specific allocation path as well.
- Extracted factory `libcfapi.so`, `CfGetMacByType` at `0x3db38`,
  `CfGetMacForIF` at `0x3d6e8`, and `GetMacCore` at `0x3d3c0`: allocate from
  the MAC database according to interface and use state.
- `CfPdtGetMacForIF` at `0x3e134` explicitly requests MAC index 2 for the
  configured secondary WAN. This does not make `0x0101` a LAN-specific tag.

These findings establish the source of factory MACs and an observed default.
They do not establish a fixed factory assignment for every WAN mode. The
mainline policy is now explicitly WAN=`0x0100`, LAN=`0x0101`, as requested by
the user. It supersedes the initial shared-address implementation and does
not claim that the second tag was universally reserved for LAN by the vendor.

## Implementation

- `linux-6.18.38/drivers/nvmem/layouts/zte-tags.c` and `zte-tags.h` parse and
  validate both addresses before registering the `mac-address` and
  `mac-address-lan` cells. Their offsets are discovered from records, never
  hard-coded.
- The parser checks magic, exact payload length, CRC, entry bounds, unique
  requested MAC key, six-byte value and valid unicast Ethernet address. Unknown
  keys are skipped. Flash padding outside the declared payload is not parsed.
- `zx279133-sr1010.dts` attaches the layout to the tags partition and connects
  the NPPT Ethernet controller to the WAN cell and its LAN conduit child to
  the LAN cell.
- `zx279133-main.c` propagates probe deferral and factory-cell errors instead
  of silently generating a random address while configured NVMEM is missing.
  Boards without an NVMEM reference retain the existing random fallback.
- `zx279133-lan-conduit.c` applies its configured NVMEM MAC before registering
  the netdev, so DSA ports inherit the LAN address. A missing/invalid configured
  LAN MAC fails probe instead of silently retaining the WAN address.
- `port/mainline/zxdbg.fragment` builds the layout into the diagnostic kernel.
- The MTD provider and all partitions remain read-only. The current mainline
  tags device is `mtd1`; the factory's `/dev/mtdblock2` number is not reused.
- The serial acceptance helper now sends eight-byte chunks with 20 ms pacing;
  32-byte chunks caused a confirmed PL011 input overrun and lost result marker
  during this run. The failed capture was rerun successfully.

## Validation

Built with `JOBS=8 ./port/mainline/build-zxdbg.sh`, then TFTP/RAM booted:

- FIT: `out/sr1010-zxdbg.itb`, 11,013,032 bytes.
- SHA-256: `ef35d4b1319d6aeb3dac90822ce9febb4edcef34526901f0c00ba2d03ddb6dbf`.
- `out/tags-mac-acceptance.json`: WAN matches `0x0100`; all five LAN-side
  interfaces match `0x0101`; WAN and LAN addresses differ.
- `out/tags-split-parser-runtime.log`: production parser passes 15/15 RAM-only
  cases: unaligned valid input, bad magic, bad CRC, short header, bad payload
  size, oversized key/value, truncated trailing record, duplicate MAC tag,
  multicast MAC, missing base tag, and shifted MAC after an odd-sized unknown
  record, selecting the second tag, an invalid second MAC, and a missing
  second tag. The shifted record case confirms dynamic offset handling.
- WAN host-to-board ping: 3/3. Board-to-host ping: 3/3, recorded in
  `out/tags-split-final-state.log`; host ping is in `out/tags-split-wan-ping.log`.
- Windows LAN1 IPv6 ping: 3/3 to the new LAN address
  `fe80::7e7d:21ff:feb9:c13b%19`, recorded in `out/tags-split-lan-ping.log`.
- Five-port LED carrier/brightness/GPIO consistency: 5/5 in
  `out/tags-split-led-result.json`. WAN and LAN1 were connected; LAN2..4 were
  disconnected. This run did not repeat physical cable moves.
- IPv6 RSS remained enabled; only the two normal DSA modules remained after
  unloading the parser test. No BUG/Oops/WARNING/Call trace was found.
- New C sources pass strict checkpatch with zero findings; the layout schema
  and NPPT schemas pass `dt-doc-validate`, and the generated DTB passes both
  targeted `dt-validate` checks. Relevant `git diff --check` passes.

The complete 1 MiB tags partition SHA-256 still matches the pre-change baseline:
`2c1479b5c05d65021f8863476e778ce8386c813cc0ff6297ef93b1e2d0c9cb6c`.
The external test module was unloaded; it left the expected out-of-tree taint
bit (4096), which the final RAM reboot cleared. Final taint is zero, the test
module is absent, all seven MTD flags remain `0x0`, and the final log contains
no kernel fault or serial input overrun. Evidence:
`out/tags-split-mac-boot.json` and `out/tags-split-final-state.log`.
No flash write or `saveenv` was issued.

## Reproduction

Run `port/mainline/tags-acceptance/verify-tags-mac.py` with the board at the
mainline serial shell. It discovers the partition by its `tags` name, checks
CRC independently with Python zlib, compares all interface addresses, and
saves only MACs and hashes, not unrelated factory configuration values.

The optional `tags-parser-test.c` module includes the production parser and
uses synthetic RAM buffers only. Build it against `out/kernel`, stage its
`.ko` under the TFTP root, load it, inspect `tags-parser-test: PASS 15/15`, and
unload it. Its build products are cleaned from the source directory.

Source, configuration, and acceptance tools are committed separately from
generated images and unrelated bootinfo work. No remote push was requested.


## Policy change: one address for WAN and LAN

2026-10-09. The split addresses (WAN `0x0100`, LAN `0x0101`) kept every
WAN-to-LAN flow off the hardware NAT.

**Cause.** A flow entry has no source MAC field: only the destination MAC is
stored (bytes 8 to 13 of the 32-byte fast data). The hardware takes the source
MAC of a routed frame from the WANID table, field `m_cpumac`, which the driver
sets to the WAN address for both directions (WANID 0 upstream, WANID 16
downstream). The factory driver does the same: `wanid_set_mac(n, mac)` writes
one MAC to entry `n` and to entry `n + 16`. `zx279133_flow_parse()` therefore
requires the source MAC of the rewritten Ethernet header to equal the WAN MAC.
For LAN-to-WAN the egress device is the WAN, so it passes. For WAN-to-LAN the
egress device is the bridge with the LAN address, so it failed and the flow
stayed on the software path.

**Measurement** (user, WAN download, same board):

| MAC setup | Download | CPU0 / CPU1 softirq | Hardware flows |
| --- | ---: | ---: | --- |
| split addresses | 945 Mbit/s | 97.6% / 0% | upstream only |
| one address (temporary) | 947 Mbit/s | 0% / 0.2% | both directions, hit counts grow |
| split addresses again | 924 Mbit/s | 56.5% / 58.5% | upstream only |

With one address, WAN packets entering the CPU during 12 seconds fell from
about 970000 to 204. The WAN link is 1 Gbit/s, so the gain shows as CPU load,
not as speed.

**Factory.** The runtime capture in `vendor-reference/sr1010-vendor-runtime`
shows `br0`, `eth0` to `eth4` and `nbif0` all on `7c:7d:21:b9:c1:3a`.

**Change.** `zx279133-sr1010.dts` no longer gives `lan_cpu` an `nvmem-cells`
property. `zx279133_lan_netdev_setup()` already starts the conduit on the WAN
address, so the conduit, the four DSA ports and the bridge use it. The
`factory_lan_mac` cell is still defined and still validated by the layout
driver, which checks both tags before publishing either cell, so a board that
wants a separate LAN address can use it again. The offload check is unchanged
but now reports the first refusal in the kernel log, with both addresses.

**Not covered.** PPPoE and IPv6 flows go through the same check and are no
longer refused for their source MAC; neither was tested on the device for
this change. Bridging the WAN into the LAN would put one address on one
segment.
