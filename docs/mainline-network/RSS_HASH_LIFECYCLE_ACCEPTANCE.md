# Integrated IPv4 RX hash lifecycle acceptance

Successor: [standard RSS query acceptance](RSS_ETHTOOL_ACCEPTANCE.md) adds
read-only table/field reporting. FIT and interface boundaries below describe
this preceding lifecycle acceptance snapshot.

2026-09-08: the guarded PPU hash is now installed by the driver during firmware
preparation, including both AF_XDP UMEM transitions. No external hash or trace
module was loaded in this acceptance run. Startup, disable/re-enable comparison,
XDP, zero-copy completion and hardware-flow coexistence pass 53 independent
checks. This closes the installation lifecycle gap after
[per-interface worker acceptance](RX_WORKER_SPLIT_ACCEPTANCE.md).

## Implementation and limits

`zx279133-np.c` accepts the calibrated firmware only when its length is 103456
bytes and CRC32 is `ef1d7647`. Its SHA-256 is
`056da8ecb66d8aa13248eb974a87b81ad97d96507fc5c526edbe2b98cc992b40`.
The 100 instructions in `zx279133-rx-hash.h` match the previously saved hardware
readback. Both bodies are written before the two hooks; every four-instruction
block is read back through the calibrated result window and compared. This
happens before routing and RX start. A write/readback error fails preparation;
`rx_hash_active` is published only after complete preparation succeeds.

The read-only parameter `zx279133.rx_hash=0` selects native queues at boot.
Default is enabled. An unknown firmware fingerprint keeps native queue selection
and warns; this branch is implemented but has not been tested with a deliberately
modified firmware on the board. `ethtool -S eth0` exposes `rx_hash_active` and
`rx_hash_loads`; the actual sysfs parameter is
`/sys/module/zx279133/parameters/rx_hash`.

The fixed folded-XOR hash uses IPv4 source/destination, ports and protocol,
retains the native interface bit, and selects one of eight hardware rings.
The parser guards select nonfragmented IPv4 TCP/UDP and otherwise execute the
native queue read. WAN and LAN conduit each expose RX2/TX2, with half of each
interface's rings assigned to each NAPI worker. AF_XDP remains queue0-only and
uses the existing shared-UMEM single-worker fallback.

This is bounded IPv4 flow steering. It does not yet provide standard ethtool
RSS key/indirection configuration or skb hardware-hash reporting, and does not
establish throughput improvement. SG/TSO remain disabled. The earlier
[malformed-frame limits](RSS_FLOW_INTERACTION.md) still apply: parser predicates
are not a complete malformed-packet validator; a truncated payload can retain a
parsed tuple, and unmatched traces do not establish a drop location.

## Board evidence

Saved records are in [integrated evidence](../../out/acceptance/rss/integrated/).
The independent [auditor](../../port/mainline/rss-acceptance/audit-rx-hash-integration.py)
recomputes checks from the recorded counters, receiver data and calibrated
instructions, rather than accepting the top-level PASS labels.

| Phase | Verified behavior |
| --- | --- |
| Default startup | active=1, loads=1; WAN-only and LAN-only each receive 4096 ordered packets; dual-port receives 8192, without missing/bad/order/duplicate errors. Each active interface reaches both workers. |
| TCP | 16 streams of 8 MiB, 128 MiB total, byte integrity passes. |
| XDP_PASS | Queue indices 0 and 1 each receive 2048; unexpected bucket receives zero; dual-port UDP integrity passes. |
| UMEM attach | Driver reload installs generation 2 automatically; ten queue-15 frames return exact MAC-swapped bytes. Ordinary dual-port UDP remains intact on worker 0. |
| UMEM detach | RX=10, TX=10, completions=10, bytes=1120, no invalid/full/drop counters; generation 3 installs automatically. Both workers resume per-interface UDP delivery. |
| Hardware flow | Both hardware directions gain 128 packets; both endpoints verify 32768 bytes. One background CPU queue-12 descriptor was observed, so this is not a zero-CPU-descriptor claim. |
| Explicit disable | Parameter N, active=0, loads=0; warmed UDP receives 4096 per interface, exclusively native q0/q8. TCP 128 MiB passes. |
| Default recovery | Original bootargs restored; parameter Y, active=1, loads=1; UDP 8192 and TCP 128 MiB pass again. Both peer pings pass, taint=0, no kernel error signatures. |

Two earlier attempts are retained under `excluded-*` and do not count as disable
acceptance. The vendor U-Boot preserved double quotes around the entire bootargs
string, so the first attempted disable actually stayed enabled. The subsequent
correctly disabled cold-neighbor UDP run lost five LAN packets (4091 received)
with five order observations. The runner now verifies reachability before
starting receivers and the measured counter window. Warmed disable and recovery
tests pass. Cold-neighbor timing is consistent with the initial-packet pattern;
no wire capture identifies the exact loss location.

For this U-Boot, the RAM-only disable command was
`setenv bootargs ${bootargs} zx279133.rx_hash=0`, without enclosing double quotes.
No `saveenv` was used. Recovery reloaded the original environment and booted the
same FIT without the override. Test flow rules and processes were removed.

## Reproducibility and next work

Build: `JOBS=8 ./port/mainline/build-zxdbg.sh`; focused driver `W=1` build passes
without warnings/errors. Header strict checkpatch passes; two unrelated existing
SMMU alignment CHECKs remain in `zx279133-np.c`.

Current `out/sr1010-zxdbg.itb` and `out/sr1010-rx-hash-integrated.itb` are
10,997,772 bytes, SHA-256
`72b4334bd50e40b8814856a7047593c046c9201fa606397498d41e8dadba8924`.
The preceding worker-only image is preserved as
`out/sr1010-pre-integrated-hash.itb`, 10,997,768 bytes, SHA-256
`4622490d6996e3162d9f8094ace06ea690bc8383741cea45513b4d49db088de5`.

Run `python3 port/mainline/rss-acceptance/audit-rx-hash-integration.py out/acceptance/rss/integrated`.
The saved [audit result](../../out/acceptance/rss/integrated/audit.json) has 53
passing checks. All board work was RAM-only; host topology was preserved.

Next, define which standard RSS controls this fixed algorithm can truthfully
support, then implement the supported configuration and hash-reporting contract
with queue/packet evidence. Performance acceptance follows that contract; it is
not inferred from packet distribution alone.
