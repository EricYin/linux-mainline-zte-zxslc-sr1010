# Standard RSS query acceptance

Successor: [writable configuration acceptance](RSS_CONFIGURATION_ACCEPTANCE.md).
The setter limitations below describe this earlier read-only snapshot.

2026-09-08: WAN `eth0` and LAN conduit `lan-cpu0` now expose the active fixed
PPU hash through standard ethtool RSS queries. The board passes 83 independent
checks across normal startup, AF_XDP UMEM attach/detach, explicit hash disable,
and default-on recovery. This adds a read-only standard interface; programmable
RSS configuration and skb hardware-hash reporting remain unimplemented.

## Interface contract

| Query or operation | Implemented behavior |
| --- | --- |
| `ethtool -x eth0` / `lan-cpu0` | Two Linux RX queues; eight effective hash buckets `0 0 0 0 1 1 1 1`; XOR on, Toeplitz/CRC32 off; no key or input transformation. |
| `ethtool -n DEV rx-flow-hash tcp4` / `udp4` | IPv4 source, destination, protocol, source port and destination port. |
| Other supported flow-type queries | No hash fields; IPv6 TCP/UDP were checked on the board. |
| Shared AF_XDP UMEM active | All eight entries report Linux queue 0, matching the existing RX queue-recording fallback; hardware hashing remains active. |
| Hash disabled or hardware unprepared | Table query returns `EOPNOTSUPP`; field queries return no active fields. |
| Table, key, function or field modification | Unsupported; no setter is installed. Table and field writes were checked on both interfaces. |
| Nondefault RSS context | Unsupported. |

The table is the effective mapping from the three-bit hash to Linux delivery
queues; it is not a newly discovered writable hardware table. The fixed algorithm
and nonfragmented IPv4 TCP/UDP/parser boundaries remain those in
[integrated lifecycle acceptance](RSS_HASH_LIFECYCLE_ACCEPTANCE.md).
The ethtool message `RSS hash key: Operation not supported` is expected for this
keyless algorithm and does not mean the table query failed.

Parent callbacks hold the datapath mutex while reading active hash/UMEM state
for the table. The LAN child forwards queries through its typed service boundary,
without exposing parent internals. Hash fields use the published active state.
DSA user ports are not presented as additional hardware RSS interfaces.

## Evidence

Records: [ethtool evidence directory](../../out/acceptance/rss/ethtool/).
The [collector](../../port/mainline/rss-acceptance/query-rss.py) captures raw query
output and exit status, including rejected writes. The
[auditor](../../port/mainline/rss-acceptance/audit-rss-query.py) checks the actual
fields/table, receiver contents and counters. Run:

```sh
python3 port/mainline/rss-acceptance/audit-rss-query.py out/acceptance/rss/ethtool
```

The saved [audit](../../out/acceptance/rss/ethtool/audit.json) passes 83 checks.
Normal, UMEM, disabled and recovery phases each receive 8192 ordered UDP packets
without missing, bad, duplicate or order errors. Normal/recovery traffic reaches
both workers for each interface. UMEM ordinary traffic runs on worker 0.
Disabled traffic uses only q0/q8: q0 gains 4096 and q8 gains 4105 descriptors,
including nine additional descriptors beyond the measured WAN payload packets.
The audit permits background traffic in native queues but requires other queues
to remain unchanged. Following UMEM detach and after final recovery, TCP passes
16 streams of 8 MiB, 128 MiB per run, with no driver error deltas.

Generation counters follow 1 → 2 → 3 across startup/attach/detach, 0 when
explicitly disabled, and 1 after recovery. The recovery table was also checked
using `--disable-netlink`; legacy ioctl and the default netlink path agree.
This turn checked ordinary traffic during UMEM attachment; the preceding
lifecycle acceptance owns the separate zero-copy echo/completion evidence.

One disabled UDP capture is retained as `excluded-off-console.json`: a kernel
`crng init done` message split the receiver summary in serial output. Re-reading
the board receiver files showed both complete PASS summaries; a fresh bounded
run then passed with intact capture. The excluded capture is not counted.
During final recovery, TFTP needed longer than the initial 45-second wait due
to retries; the complete 10,997,816-byte transfer was verified before `bootm`.

## Build, recovery and remaining work

`JOBS=8 ./port/mainline/build-zxdbg.sh` and the focused driver `W=1` build pass
without compiler warnings/errors. Scoped `git diff --check` and Python parsing
pass. Checkpatch finds no new alignment checks; the two existing hash-stat
`ethtool_sprintf` preferences from the preceding turn remain.

Current `out/sr1010-zxdbg.itb`: 10,997,816 bytes, SHA-256
`030a4a1380fb4c55f6d80b2cf4305ddb8e1ec99fb1d28c1f1deb06b00ca860b4`.
The preceding accepted integrated image remains at
`out/sr1010-rx-hash-integrated.itb`. Final board state is default hash enabled,
active=1, loads=1, taint=0, IRQ masks 1/2, both peer pings successful, and no
kernel error signatures. No external hash module, persistent bootargs change,
flash write, host topology change or Git commit was performed.

Writable indirection requires an actual supported steering implementation and
lifecycle evidence. Hardware RXHASH requires locating a hash value in DMA-visible
metadata and proving its packet association; software recomputation is not a
substitute. These are separate next gates, followed by performance acceptance.
SG/TSO remain disabled.
