# Writable RSS configuration acceptance

Subsequent integration and current correctness boundaries are recorded in
[RSS_CLOSURE_ACCEPTANCE.md](RSS_CLOSURE_ACCEPTANCE.md). The unfinished-work
statements below describe this earlier configuration stage.

2026-09-08: WAN `eth0` and LAN conduit `lan-cpu0` now accept independent
eight-bucket RSS indirection settings through the standard ethtool interface.
The driver installs the calibrated programmable PPU bodies and retains both
maps across shared-engine/AF_XDP reloads. The independent audit passes 77 checks.

This implements the configuration gate after the
[hardware map prototype](RSS_PROGRAMMABLE_MAP_PROTOTYPE.md). Hardware skb hash
reporting, IPv6 RSS and performance acceptance remain separate unfinished work.
SG/TSO remain disabled.

## Supported contract

| Operation/state | Behavior |
| --- | --- |
| Standard `ethtool -X DEV equal 2` | Configures alternating buckets `0 1 0 1 0 1 0 1`; tested through the default netlink path. |
| Explicit eight-entry table | Each bucket selects Linux RX queue 0 or 1; WAN/LAN settings are independent. The acceptance helper uses standard `ETHTOOL_SRSSH`, not a private driver command. |
| Initial default | `0 0 0 0 1 1 1 1` on both interfaces. Boot defaults differ from ethtool's generic alternating `equal 2` request. |
| Same table again | Success without restarting the engine or incrementing `rx_hash_loads`. |
| Shared AF_XDP UMEM attached | Query reports effective queue 0; writes return EBUSY. Saved normal maps are reinstalled and reported after detach. |
| Existing hardware-offloaded flows | A changed map returns EBUSY before resetting anything. An identical table remains a no-op. |
| Hash disabled/unavailable | Writes return EOPNOTSUPP; inactive table queries remain unsupported. |
| Key, Toeplitz/CRC32, field changes, nondefault contexts | Unsupported. The algorithm remains keyless folded XOR for guarded IPv4 TCP/UDP. |

Reconfiguration pauses both interfaces, drains TX, releases the shared engine,
installs/verifies the configured microcode, then resumes RX/TX. It can interrupt
traffic and renegotiate WAN link; this is not a lossless live-remapping claim.
Runtime maps persist through engine reloads, not through a reboot or driver
removal. DSA user ports are not additional hardware RSS interfaces.

## Implementation

`zx279133-rx-hash.h` contains the 416 calibrated instructions and 32 leaf-map
descriptors. The loader changes only the selected queue bit in each leaf.
Reconstructing the asymmetric maps from the production header matches all 416
previously read-back hardware words. Each installed four-instruction block is
read back before the routing/RX start boundary.

`zx279133_datapath_suspend/resume` share the existing AF_XDP engine lifecycle.
The two cached map bytes live in the parent Ethernet device; the LAN conduit
uses its typed service callbacks. A failed application restores the old cache
and retries preparation. The requested new setting returns an error even when
rollback succeeds.

If firmware remains valid but its fingerprint is uncalibrated, rollback can
resume the established native RX path without advertising active hashing.
This preserves connectivity while retaining the old maps for a later successful
reload. Replacing the firmware file alone does not reload the engine. A plain
port down/up may also leave the engine referenced by DSA; the acceptance used
an AF_XDP attach/detach to trigger the full reload and recover the saved maps.

Shared reset clears hardware flows, so the setter rejects changed maps when
the offload table is nonempty. Flow insertion now takes the datapath mutex
before the offload mutex, closing the insertion-versus-reset window. This stage
does not add automatic replay of existing hardware flows during RSS changes.

## Board acceptance

The [evidence directory](../../out/acceptance/rss/config/) contains raw queries,
setter results, per-flow counters, packet verification and failure captures.

| Test | Result |
| --- | --- |
| Fixed WAN=ff / LAN=00 | 8192 ordered UDP packets; each expected physical ring receives its payload share. Background descriptors are retained and not mislabelled as payload. |
| Independent asymmetric WAN=a5 / LAN=3c | Queries agree with each interface's setting; eight separately measured flows on the final image each deliver 512 packets per interface to the independently predicted hardware ring. |
| No-op writes | Two unchanged-map requests succeed with no load-generation increase. |
| Standard CLI | `ethtool -X eth0 equal 2` succeeds; LAN remains unchanged. Toeplitz and field changes are rejected. |
| UMEM lifecycle | Writes while attached return errno 16; attach/detach reloads preserve a5/3c, with effective all-zero queue reporting during attachment. |
| One-read firmware fault | New setting returns errno 95; restored valid firmware is read on rollback, old a5/3c becomes active again, with one successful load-generation increment. |
| Persistent fingerprint mismatch | New setting returns errno 95; native RX remains available and 8192 UDP packets pass. Valid-file restoration plus a full engine reload recovers a5/3c. |
| Hardware flow protection | Both changed-map requests return errno 16 without increasing the load counter; before and after rejection, both hardware flow directions gain 128 packets and endpoints verify 32768 bytes. Removing those test flows allows configuration. |
| Post-rollback TCP | 16 streams × 8 MiB = 128 MiB, all bytes verified. |
| Final default RAM recovery | Both maps f0, active/load counters 1/1; 8192 UDP packets and 128 MiB TCP pass; both peer pings pass; taint=0 and kernel error scan empty. |

The audit recomputes flow-to-ring predictions, checks actual endpoint content,
deduplicates repeated TC action counters and verifies state transitions rather
than trusting top-level PASS labels. It passes
[77 checks](../../out/acceptance/rss/config/audit.json):

```sh
python3 port/mainline/rss-acceptance/audit-rss-config.py out/acceptance/rss/config
```

## Failure history and validation limits

The first firmware-fault fixture depended on file notifications unavailable in
this kernel. Both reads therefore saw the intentionally mismatched RAM file.
The earlier implementation also required hashing during rollback and left both
devices detached. Attempting port recovery then caused a board reset; the serial
capture contains no panic explanation, so the reset's exact cause is not proven.
`rollback.json` and `earlier-double-failure-reset.json` preserve that unsuccessful
attempt. They are excluded from successful rollback acceptance.

The implementation was changed to permit valid native firmware during rollback.
The corrected persistent-mismatch test retained working RX and its subsequent
port cycle did not reset the board. The one-read fixture was replaced by a child
process launched with the configuration ioctl: it restores the valid RAM file
after one second, within this board's observed two-second SerDes preparation
window. The log confirms the first read was uncalibrated and the rollback read
installed the valid program; the firmware SHA-256 was checked afterward. This
is a bounded board fixture, not a general fault-injection mechanism. Arbitrary
allocation/MMIO failures and failure of both valid-hardware recovery attempts
are not exhaustively tested.

One early asymmetric setter summary was split by a concurrent SerDes console
message. `asymmetric.json` retains the capture failure; the full table and
generation already showed installation. `asymmetric-confirm.json` verifies the
same setting without another restart. The collector now reads setter output
from a temporary file. An initial degraded-mode traffic probe ran before link
settled; after carrier was verified, the measured native-queue run passed.

Basic configuration/UMEM records were collected before the rollback fix;
rollback/native-fallback records use the corrected implementation. The final
image additionally guards hardware flows. Final-image flow protection, netlink
configuration, all eight asymmetric flows and default recovery were rerun.
No result from the earlier reset is counted as a successful recovery.

## Build and current board

`JOBS=8 ./port/mainline/build-zxdbg.sh` and the focused `W=1` driver build pass
without compiler diagnostics. Scoped whitespace and Python parsing checks pass.
Checkpatch retains the two preceding hash-stat `ethtool_sprintf` preferences;
there are no new alignment checks in the changed driver diff.

Current `out/sr1010-zxdbg.itb`: 11,001,964 bytes, SHA-256
`66bdbe1757b0aed66207269e1fc1afe61cb8ff1b32e6f5b47acf1c6d15de2878`.
Firmware SHA-256 remains
`056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40`.
All test flows and their newly created clsact qdiscs were removed before final
RAM reboot. No external diagnostic module was used in this configuration stage;
no flash/NAND/saveenv, host address/route change or Git commit was performed.

The [collector](../../port/mainline/rss-acceptance/run-rss-config.py) applies
optional maps and records both conduits. The
[ioctl helper](../../port/mainline/rss-acceptance/rss-set.c) accepts `DEV MASK`;
its additional firmware-path arguments are only for the documented RAM fault
fixture. Existing `query-rss.py` is a historical read-only-stage test and includes
write attempts: do not use it as a read-only collector on this writable image.

Next: investigate DMA-visible hash metadata for truthful skb RXHASH reporting,
and measure throughput/CPU cost of the larger PPU program. IPv6 RSS and further
reset/error coverage are not implied by this acceptance.
