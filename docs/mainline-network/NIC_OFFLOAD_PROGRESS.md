# NIC offload implementation and acceptance

## Final scope, 2026-09-10

The user ended this investigation. Keep the accepted RX checksum and
RSS/multiqueue implementation; leave SG/TSO/TSO6 disabled and blocked.
No further hardware exploration is scheduled.

The current production RSS result is [RSS closure](RSS_CLOSURE_ACCEPTANCE.md),
including guarded IPv4 hashing, writable WAN/LAN maps and RX worker integration.
The historical milestones below describe earlier states, not current feature flags.
The final [TSO PPU trace](TSO_PPU_CONSUMER_TRACE.md) proves template header and
tail-length updates, but not distinct-payload advancement or final source release.
Its recovery verified UDP 8192 packets and TCP 128 MiB; board state is a
2026-09-09 measurement, not a new measurement at submission time.

## Historical investigation milestones

Latest RXHASH investigation: [DMA hash export](RSS_DMA_HASH_EXPORT.md) validates
full PPU hash transfer into descriptor metadata, packet-byte preservation and
fallback controls. The 128-check audit passes. Production RXHASH remains off;
the existing writable RSS steering implementation and FIT are unchanged.

Latest production RSS result: [configuration acceptance](RSS_CONFIGURATION_ACCEPTANCE.md)
adds standard writable WAN/LAN bucket maps with reload preservation, tested
firmware-fault recovery and hardware-flow protection. Final default RAM recovery
and the 77-check audit pass. Hardware RXHASH metadata and performance remain open.

Latest programmable steering result: [bucket-map hardware prototype](RSS_PROGRAMMABLE_MAP_PROTOTYPE.md)
passes 188 checks for calibrated instructions, actual per-flow queue assignment,
register/fallback behavior and clean RAM recovery. This is a temporary module;
the accepted FIT and read-only production RSS interface remain unchanged.

Latest interface result: [RSS ethtool acceptance](RSS_ETHTOOL_ACCEPTANCE.md)
provides standard read-only RSS table/field queries on WAN and LAN conduit.
The 83-check normal/UMEM/off/recovery acceptance passes. Writable steering,
hardware-hash metadata and performance remain separate open gates.

Latest result, 2026-09-08: [integrated hash lifecycle](RSS_HASH_LIFECYCLE_ACCEPTANCE.md)
installs guarded IPv4 hashing during startup and both UMEM transitions. Default,
disabled and recovery UDP/TCP tests, XDP, AF_XDP and flow coexistence pass 53
independent checks without an external diagnostic module. Standard RSS controls,
skb hardware-hash reporting and performance acceptance remain unfinished.

Preceding driver result: [RX_WORKER_SPLIT_ACCEPTANCE.md](RX_WORKER_SPLIT_ACCEPTANCE.md)
implements two RX queues and per-interface multi-CPU receive on WAN/LAN
conduits. Ordered UDP, TCP, XDP queue identity and AF_XDP fallback acceptance
pass. Microcode hash installation/reload remains the next production gate.

Latest RSS coexistence/lifecycle work: [RSS_FLOW_INTERACTION.md](RSS_FLOW_INTERACTION.md)
adds live microcode verification and proves the tested hardware UDP NAT flow
bypasses hash hooks. Off/on/off echo, interface down/up, 8192 UDP packets and
64 MiB TCP per port pass; recovery is clean. Malformed-frame validation remains
bounded and per-interface multi-CPU RX is not yet implemented.

Preceding 2026-09-07 format result: [RSS_FORMAT_FALLBACK.md](RSS_FORMAT_FALLBACK.md)
adds verified format predicates and native fallback to continuous PPU hashing.
Twenty-five format traces and 375 independent checks pass; continuous 8192-packet
UDP and 64-MiB-per-port TCP tests pass again, followed by clean RAM recovery.
Production integration, offloaded-flow/parser edge cases and per-interface RX
worker distribution remain unfinished. No RXHASH/RSS controls are advertised;
the normal FIT remains unchanged.

User order: checksum/SG first, then RSS/multiqueue, then TSO/TSO6. QoS and unrelated software features are out of scope. RAM-only board work; no flash writes, saveenv, host address changes, or unrelated Git edits.

2026-09-07 user decision supersedes the original SG gate: **SG is BLOCKED**;
proceed with RSS/RX multiqueue now. TX multiqueue already has acceptance evidence
below. SG remains disabled and requires new hardware-contract evidence to resume.

RSS stage update: per-hardware-RX-queue statistics are implemented. Protocol 84
LOOKUP_MISS steering passed RAM A/B/A: WAN queue 8 → 9 → 8, LAN 0 → 1 → 0,
with WAN/LAN 8-flow, 16 MiB integrity tests passing on probe and recovery.
WAN/LAN interrupt groups route independently to source 2/0 and now run separate
NAPI consumers with separate page pools and synchronized shared refill/map state.
Simultaneous WAN/LAN traffic passed 64 MiB per port in each direction, with
verified overlapping polls on separate CPUs. A padded LAN TCP checksum fallback
also fixes an existing inbound-transfer stall. Production RSS remains unfinished.
Default CPU placement now splits the IRQs without manual test configuration;
the diagnostic initramfs no longer overrides both to CPU1. User affinity choices
survive interface stop/start. Temporary protocol table edits are removed.
Current FIT is 11,001,964 bytes, SHA-256
`66bdbe1757b0aed66207269e1fc1afe61cb8ff1b32e6f5b47acf1c6d15de2878`;
older image entries below are historical acceptance snapshots. See
[RSS_RX_MULTIQUEUE.md](RSS_RX_MULTIQUEUE.md) for evidence and the next gate.
Earlier affinity and dual-NAPI acceptance are in
[RX_AFFINITY_ACCEPTANCE.md](RX_AFFINITY_ACCEPTANCE.md) and
[RX_DUAL_NAPI_ACCEPTANCE.md](RX_DUAL_NAPI_ACCEPTANCE.md); the preceding design
audit is [RX_NAPI_OWNERSHIP.md](RX_NAPI_OWNERSHIP.md).

Earlier RSS hardware work: [RSS_PPU_QUEUE_CONSUMER.md](RSS_PPU_QUEUE_CONSUMER.md)
identifies PC1558/1893's fixed queue read and its downstream SPR16 transform.
Single matched WAN/LAN contexts were moved to RX queues 9/1, each with exactly
one descriptor observed there. This remains a temporary diagnostic; hash
computation and production RSS are unfinished. The board was freshly RAM-booted
back to the unchanged current image and passed 64 MiB per-port incoming integrity.

## 本次结果

2026-09-07 最新 SG 诊断：六组分段线端测试的 128 字节整包对照全部通过，
但都未收到两段组成的完整帧。完整 RXD 标记会把前半段独立发成 64 字节帧；
候选 SOP/EOP 分段标记则产生两条 NP1 `0x62/0x10` 诊断。原厂 LS 位置、
保留 WOE 控制位以及两个上下文同时放行均未实现 SG。仍缺少多缓冲区
关联协议、正常提交方式和最终 DMA 回收，SG 保持关闭。详见
[SG_WIRE_SPLIT_TESTS.md](SG_WIRE_SPLIT_TESTS.md)。板子已恢复，
WAN/LAN 各 8 流、16 MiB、零错误字节；未改动生产驱动或 RAM FIT。

| 项目 | 当前结果 |
| --- | --- |
| RX checksum | 已实现并上板验收：基本 IPv4/IPv6 TCP/UDP，单层 VLAN；封装、分片、扩展头、零 UDP checksum 和 XDP 有明确的软件回退边界 |
| TX checksum | 保留已验证的 IPv4 TCP 硬件路径；其它协议走软件回退，失败的 UDP 探测代码已删除 |
| SG | BLOCKED（用户于 2026-09-07 确认）；缺少续帧/完成契约，独立 DMA 也缺少控制器入口；保持关闭 |
| TX 多队列 | 已实现并验收：WAN、LAN conduit 各 2 条 Linux TX 队列，对应 IDM 硬件队列 1、2；ring、完成计数、BQL 和统计独立 |
| RSS / RX 多队列 | WAN/LAN 独立 NAPI 已实现并通过双 CPU 并行验收；每接口仍 RX1/TX2，RSS 流哈希与接口内多 RX 队列尚未实现 |
| TSO / TSO6 | 未实现；原厂普通 GSO 会复制分段，upload 模式的重复模板协议不足以实现任意用户数据的硬件分段 |

**整个请求尚未全部完成。没有 SDK 不代表硬件不支持，以上未完成项的边界是硬件协议尚未恢复。** 双 TX 队列仍共用短暂的提交锁；本次不宣称吞吐量或多 CPU 性能提升。

最终 RAM 镜像：`out/sr1010-zxdbg.itb`，10,996,596 字节，SHA-256
`8c74959267c363986cc59e6dcca216204857cce6198ddff580bda57a5beb4e53`。
已运行该镜像，未写入 NAND/flash，也未保存 U-Boot 环境。

## Baseline

- Outer commit `2a41799c2`; nested kernel `13d2829ee`, branch `codex/sr1010-mainline`. Kernel worktree was clean at start. Preserve staged outer `.codex/config.toml`, `.gitignore`, renamed `AGENTS.md.ignore`, and IDA databases.
- Original known-good FIT copied to `out/sr1010-nic-baseline.itb`.
- Board serial: tmux `sr1010-serial:0`, `tio` on `/dev/cu.usbmodem5B140649641`.
- Host WAN: en8, 192.168.1.100, MAC `00:e0:41:68:0d:86`, IPv6 link-local `fe80::1436:647a:428:39e5`. No bridge101 or QEMU currently. Existing tcpdump PID 51578 is unrelated; do not kill it. Background host applications sometimes send unrelated WAN traffic: packet fixtures must filter their own cases; no throughput claims from these runs.
- Windows LAN peer: SSH `cnjn@192.168.3.43`; Ethernet 192.168.5.100, MAC `f8:89:3c:26:fe:02`, Npcap device `\Device\NPF_{908BA7F3-F82E-4E1E-92C6-573623C048F9}`. Python and Npcap are installed. Temporary fixture copied to `C:/Users/cnjn/AppData/Local/Temp/sr1010-nic-checksum-packets.py`.
- TFTP: existing dnsmasq serves `out/`. RAM load is `tftpboot sr1010-zxdbg.itb`, verify completion, then `bootm`. Board MAC changes every boot; read it again.

## Confirmed hardware observations

1. WAN and LAN XMAC HW_FEATURE0 (word-addressed offsets) are `0x0401310a`. WAN version at `0x19180440` is `0x00107630`; feature0 at `0x19180470`; feature1/2/3 at `0x19180480/490/4a0` are zero. Upstream dwxgmac2/xgbe headers identify feature0 bit16 as RXCOE, bit14 as TXCOE; feature1 bit18 TSO and bit20 RSS. The XMAC native TX/RSS/TSO flags do not establish these capabilities; NPPT/IDM is a separate engine.
2. XMAC RX_CTRL is standard DWC RX_CONFIG with register offsets multiplied by four. Current driver programmed `0x3e800086`, running value `0x3e800087`: IPC bit9 was disabled.
3. With IPC disabled, deliberately good/bad IPv4 TCP/UDP, bad IPv4 header checksum, and good/bad/zero IPv6 UDP all reach IDM with indistinguishable protocol metadata. Descriptor byte14 values `0x15/0x0d/0x13/0x0b` mean IPv4 TCP/UDP and IPv6 TCP/UDP, not checksum validity.
4. Setting WAN `0x19180010=0x3e800287` and LAN `0x19140010=0x3e800287` filters bad checksums before IDM. Both paths passed controlled good/bad tests. IPv4 UDP zero checksum remains admitted; IPv6 UDP zero is filtered. LAN CPU transport remains functional.
5. TX checksum bit15 with normal IP/TCP offsets on UDP changes payload bytes 8/9 (TCP checksum position) and leaves UDP checksum incorrect. Tested both IPv4 and IPv6, payload sizes 64/127/190/253. Metadata0 bit0/bit8 and length_flags bit30/31 variants also failed. The 72-frame capture contains eight valid software-baseline frames and 64 failed probe frames. Existing IPv4 TCP hardware checksum and other-protocol software fallback are retained.

Raw evidence: `out/acceptance/20260905-nic-offloads/rx-metadata-first.txt`, `rx-ipc-wan-lan.txt`, `tx-checksum-probe.txt`, `tx-udp-probe.pcap`. The task-owned tcpdump was stopped cleanly after TX experiments.

## RX checksum implementation and acceptance

- `zx279133-mac.c` and RTL driver enable RX IPC by default before receiver enable.
- `zx279133-main.c` advertises RXCSUM; LAN conduit inherits features.
- `zx279133-idm.c` has a conservative RX checksum trust helper for unfragmented basic IPv4/IPv6 TCP/UDP with at most one VLAN tag. Zero UDP checksum, IPv4 options, IPv6 extension headers, encapsulations, XDP-run packets remain CHECKSUM_NONE. Only physical source ports 5/6 qualify. Adds `rx_hw_csum_packets` ethtool counter.
- Present design keeps MAC error filtering enabled; ethtool RXCSUM toggles whether the stack trusts the checked result. This deliberate behavior avoids trusting old unchecked descriptors across a live hardware-toggle boundary, but must be assessed/documented as a limitation.
- Both descriptor and TX checksum probe parameters were removed before the accepted RX build.
- Accepted RX FIT: `88d35a3d24246523d428d2fdcaf118f3014f5d290281708b49b6adbd18bc7c4d`, 10998000 bytes, preserved as `out/sr1010-nic-rx-accepted.itb`. Full build, ZTE `W=1`, and checkpatch passed.
- On this exact FIT, `nic-rx-accept.py on/off/extended/xdp/lan` all returned PASS. Each plain run injects 33 cases and receives 15 valid packets; on/LAN mark 12 as hardware checked, off/XDP mark none. IPv4 UDP zero remains unchecked. The extended run injects 55 cases across VLAN/QinQ/options/fragments/PPPoE and asserts only eligible single-VLAN packets are marked. GRO is disabled on the conduit and DSA user interface during metadata tests, so software GRO cannot masquerade as hardware checksum verification.
- GRO and RXCSUM were restored, XDP detached, WAN and LAN pings each passed 3/3, and the kernel error scan was clean. Evidence: `rx-{on,off,extended,xdp,lan}.txt` and `final-rx-recovery.txt` under the acceptance directory.
- Fixtures: `port/mainline/network-acceptance/nic-checksum-packets.py` uses libpcap/Npcap for exact good/bad TCP/UDP frames, supports plain/VLAN/QinQ/PPPoE/options/fragments; newest format extensions not yet copied to Windows. `nic-udp-send.c` sends four known UDP payloads, including odd lengths; accepts IPv6 link-local destination on eth0. Build binary is `out/nic-udp-send`.

## Current execution state

- Queue-contract probe FIT: `8f50aba35ed94299ff8583b00f02ffe5df98d357502ab8ac57dc122c4348ce0e`, 10997776 bytes. Its temporary `tx_queue_probe` parameter has now been removed from source.
- Hot switching the queue with both interfaces down produced completions but no peer replies for queues 0/2/3; queue 1 also failed after switching back. The DSA child still owns the shared datapath while its netdevs are down, so changing a global queue number does not reinitialize ring ownership. These results are invalid as queue-capability conclusions. Fixed-queue boot tests below supersede them. Raw serial: `queue-warm-probe-serial.txt`.
- Fixed queue-2 boot succeeded: WAN and LAN each passed 3/3 after carrier became ready. Eight simultaneous TCP streams transferred 16,777,216 bytes with zero wrong bytes and valid peer acknowledgements; TX checksum hardware counter increased and no TX errors/timeouts occurred. Evidence: `queue2-boot*.txt`. Thus queue 2 can carry ordinary WAN/LAN packets; the hot-switch failure is not a hardware limitation.
- Two-TX-queue implementation is now in source and builds cleanly. WAN and LAN conduit allocate two Linux TX queues mapped to hardware queues 1 and 2. Each ring has its own slots, producer/consumer, completion count, pending doorbell, submitted/completed statistics, and per-netdev BQL accounting. Shared TX spinlock remains; no parallel-CPU performance claim. XDP/AF_XDP retain Linux queue 0. RX remains one NAPI, and RSS is not advertised.
- Multiqueue FIT `975bb2de043fe4d4d86d5c79af744a842945c605cd4240bd9a7bbba0de9c3714`, 10997832 bytes, passed simultaneous-ring acceptance. `nic-mq-accept.py` uses two peer ports, CPU-pinned senders and XPS; eight streams each carry nonzero deterministic data verified byte-for-byte. WAN and LAN each passed 16 MiB. Fixed queue 0 and queue 1 WAN runs each passed 96 MiB, with respectively 71,566 and 71,780 completions, proving descriptor reuse and 16-bit counter wrap. The unused ring did not advance during either fixed-queue WAN run. Fixed queue-0 LAN also passed 16 MiB. Every run drained all outstanding descriptors with zero TX errors/timeouts.
- Native XDP_TX passed ten exact echoed-packet comparisons on that FIT, with zero pending TX/aborted XDP afterward. Use destination IP `192.168.1.100` in MAC-swap echo tests: sending a reflected packet back to the board's IP lets the host's existing routing forward it again and decrement TTL, contaminating the fixture.
- AF_XDP exposed a LAN watchdog false alarm during shared IDM/UMEM restart. Both netdevs are now detached while replacing the pool and reattached after successful initialization or rollback. On the final FIT, the filtered fixture received, echoed and completed exactly 10 packets / 1,100 bytes. All ten peer frames matched byte-for-byte; no invalid descriptors, refill deficit or watchdog error occurred. Evidence: `mq-xsk.pcap` and `mq-xsk-board.txt`.
- The XSK fixture accepts optional `UDP_PORT` and flushes readiness. `xsk-zc eth0 120 49000` preserves ARP and unrelated traffic in Linux. Its original unfiltered mode intercepts all traffic and is unsuitable for judging neighbour recovery on this active host. UMEM changes still reset the shared engine and renegotiate WAN; early probes can be lost during recovery. Subsequent WAN/LAN pings passed 3/3 automatically, and normal TCP transfer passed without another interface reset.
- Final FIT `8c74959267c363986cc59e6dcca216204857cce6198ddff580bda57a5beb4e53` reports RX 1 / TX 2 on both WAN and LAN conduit. All five RX checksum suites passed again on this exact image. WAN and LAN each passed another 16 MiB concurrent integrity run; both queues drained with no errors or timeouts. Interface down/up also recovered both sides. Evidence: `mq-final-prepare.txt`, `rx-*.txt`, `mq-{wan,lan}-result.json`, `mq-final-link-cycle.txt`, and `final-recovery.txt`.
- Final full build, ZTE `W=1`, checkpatch (0 errors/warnings/checks), and both Git whitespace checks passed. GRO/RXCSUM and the original XPS masks were restored; test BPF programs detached, sockets closed and task-owned captures stopped. Source has no temporary descriptor, checksum or queue probe parameter.
- Full build command: `./port/mainline/build-zxdbg.sh > /tmp/sr1010-nic-build.log 2>&1`, existing Docker image `sr1010-stage15-builder`. Builds take around a minute. Never use host macOS make for kernel.
- TFTP executables available: `out/ethtool-test`, `out/libmnl.so.0`, `out/nic-udp-send`.
- `/sys/module/zx279133/parameters/` is correct, NOT `zx279133_eth`.
- BusyBox `ip neigh replace` is unsupported. For TX IPv6 tests use host's existing link-local address with `nic-udp-send`; it sets sin6_scope_id for eth0.

## Unimplemented parts of the request

1. **SG DMA:** the normal CPU descriptor contains one address and length. Factory `net_tcp_gso_tx()` copies the linear area and fragments into separate contiguous nbuf packets. The BMU/ODMA buffer indices do not establish an arbitrary scatterlist interface. A segment-link format, DMA ownership/completion contract, and noncontiguous-payload board test are still missing; `NETIF_F_SG` remains off.
2. **RSS / RX parallel queues:** the recovered protocol-action table selects a fixed CPU queue. No flow-hash key, indirection table, or RX hash metadata contract has been recovered. RX page pool, buffer ownership map, refill ring and NAPI currently have one owner. They cannot simply be polled simultaneously from two CPUs. The completed two-TX-ring work does not claim RSS or multiple RX NAPI instances.
3. **TSO/TSO6:** `net_tcp_gso_tx_upload()` copies only headers and clears one MSS-sized payload buffer, while `net_gso_upload_send()` supplies repeat count, MSS and final length. This provides a repeated-template path, not an established contract for segmenting arbitrary user payload. Normal TSO flags remain off; Linux software GSO/GRO remain available.
4. **Additional TX checksum protocols:** the recovered and tested hardware checksum mode is IPv4 TCP. UDP IPv4/IPv6 probe modes failed data-integrity checks and were removed. Other TX protocols retain software fallback.

Relevant plat functions: `cpu_net_rx`, `dump_net_desc`, `net_cfg_desc_by_skb`, `net_tcp_gso_tx`, `net_gso_tx`, `net_gso_upload_send`, `net_tcp_gso_tx_upload`, `idm_cpu_tx`, `idm_wifi_tx`. np `pro_str` indexes 106 fixed 32-byte `pro_name` strings; `pro_table_set/get` encode CPU queue in per-port protocol actions. No standard RSS contract has been established.

The user confirmed there is no SDK beyond `vendor-reference`. Inspection of DMA/BMU/ODMA found internal 14-bit buffer indices and fixed-size reserved buffer pools, not arbitrary CPU scatterlists. `kernel-2b5.elf` exports their sizes and reserved-memory interfaces; packet TX lives in `plat_132.ko`. This bounds the recovered implementation, and does not prove absence of undocumented hardware features.

SG continuation now includes a RAM-only WOE descriptor parser module in
`port/mainline/sg-acceptance`. On the board, one complete descriptor advanced RD
to 1; adding LS=0 / LS=1 descriptors advanced RD to 3 and the slice counter to 1.
The pair still produced two records at the WOE/SIPC boundary, so payload gathering
into one Ethernet packet and final DMA ownership remain unproven. The first probe
omitted the separate common-enable bit and was corrected from `woe_enable()`.
Both experiments ended with a RAM reboot; the original FIT was reloaded.

The nbuf linked list is a packet batch; the 13-buffer WOE interface builds Wi-Fi
output descriptors. The factory rootfs and a read-only Plugin inventory did not
supply a missing WOE client. Public Intel WAV600 source supplied a concrete
payload address/length/SOP/EOP format for further comparison with the factory
microcode. See [SG DMA contract investigation](SG_DMA_CONTRACT.md) for measured
counters, precise sources, and the remaining gate. The register-table extractor
also now excludes zero-filled terminal entries. No production-driver SG feature
is enabled.

After restoring the original RAM FIT, WAN and LAN each passed eight-stream,
16 MiB byte-for-byte recovery tests, with zero bad bytes, TX errors or timeouts.
Pending TX and RX refill deficit are zero; the kernel error scan is clean.
Separate recovery evidence is in `out/acceptance/sg-dma/recovery/`.

The next WAV600 payload experiment found and corrected an ISU initialization
omission: factory `isu_init()` passes four weights `(2, 2, 1, 1)`, whereas the
earlier decompilation omitted the fourth argument. Production now initializes
`ZX279133_ISU_DWRR_WEIGHT_VALUE` to `0x01010202`. A same-packet A/B changed only
the fourth weight: RED NP1 forwarding, PPU execution and IDM SMCT receive then
advanced without descriptor resubmission. This restores basic input dispatch;
it is not a QoS implementation.

The corrected FIT is 10,997,052 bytes, SHA-256
`65b0fa1174e37603d770b075446b9631ca367daa39a1e3af6812ea3894678d29`.
The complete-frame probe reaches Wi-Fi trap queue 16 with NP1 reason/detail
`0x62/0x10` and a 32-byte diagnostic copy of the submitted descriptor. Both
unknown-station flag settings produce this result. No Ethernet payload or DMA
release completion has been verified, so the split-frame gate remains closed
and production SG remains disabled. SDT100 global Wi-Fi fields and their client
initialization remain a research lead, not an established fix. The probe and
raw evidence are linked from [SG DMA contract investigation](SG_DMA_CONTRACT.md).

After the final RAM reboot into that corrected FIT, WAN and LAN each passed
eight-stream 16 MiB byte-for-byte recovery tests (zero bad bytes). Both TX
queues completed traffic and drained; TX errors/timeouts, RX errors and RX
refill deficit were zero. WOE was disabled, Wi-Fi trap queues empty, probe
modules absent, and all four XPS masks restored to zero. The kernel scan had
no fault reports (only normal watchdog initialization matched `timeout`).
Evidence: `out/acceptance/sg-dma/recovery-wav600/`. Full FIT build, ZTE and
diagnostic-module `W=1`, strict checkpatch and Git whitespace checks passed.
The newer image has not repeated every earlier checksum/XDP/AF_XDP suite;
this continuation's recovery check covers ordinary WAN/LAN concurrent traffic.

The 2026-09-06 continuation traced NP1 detail `0x10` to firmware PC5217:
a guarded instruction-RAM marker changed only its literal and the board
returned detail `0x11`, with the same reason `0x62` and 32-byte diagnostic.
Hardware reads confirmed SDT100/101/104 definitions already exist; their
first ERAM blocks were zero. Their required field values remain unrecovered.
Four complete-descriptor variants (SID0, SID128, EP1, OWNER_FW) all reached
queue 16 with `0x62/0x10`; none verified payload delivery or release completion.
The fixture retains calibrated table reads, the explicit diagnostic marker,
and the descriptor comparisons. Unvalidated register-sampling/self-branch
code was removed and archived with the experimental captures. Production
data-path code, the factory firmware file and the FIT were not changed in
this continuation. See the dated section of `SG_DMA_CONTRACT.md` for evidence.

Final recovery on the same FIT passed WAN and LAN eight-stream 16 MiB
integrity checks, with zero bad bytes, errors, timeouts or refill deficit.
Both TX queues drained; probe modules were absent and WOE/debug/trap state
was zero. The kernel fault scan was empty. Separate evidence is in
`out/acceptance/sg-dma/recovery-np1-trace/`. Maintained diagnostic `W=1`,
strict checkpatch and both repositories' whitespace checks passed.

A further 2026-09-06 diagnostic adds an early branch control: changing only
PC5177's condition, while retaining its existing target, changes the returned
NP1 detail to `0x07`. Candidate target-field edits at PC5180 and PC5181 still
return `0x10`; they do not establish the encoding of that different instruction
family. Per-thread key reads completed but did not correlate a context to the
test frame. The transient SPR/key capture code was removed and archived.
Factory Wi-Fi port initialization was resolved to a caller-supplied value in
MF word 19 (attribute ID 71), whose meaning and required value remain unknown.
No successful payload DMA or buffer-release completion was obtained, so SG
remains unimplemented. Changes in this phase are confined to diagnostics and
documentation; the production driver, firmware file and FIT are unchanged.

After reloading the original RAM FIT, WAN and LAN each passed eight streams
and 16 MiB of exact data checks. Both TX queues drained with zero bad bytes,
errors, timeouts or refill deficit. WOE/debug/trap state was zero, SPR clearing
was restored to 1, all XPS masks to 0, and the fault scan was clean. Evidence:
`out/acceptance/sg-dma/recovery-np1-branches/`. Probe `W=1`, strict checkpatch
and Git whitespace checks passed. No flash or saved boot environment changed.

The subsequent factory-boot baseline used the user-provided `cspboot; cspstart`
sequence. Factory Linux had no active Wi-Fi NIC or WOE RX ring. Sixteen sampled
instructions matched the Intel microcode file, and SDT100/101/104 entry 0 was
also zero. Factory SPA word 19 differed from mainline on all 33 Wi-Fi logical
ports 16–48. Copying those exact values into a fresh mainline RAM boot, with
readback verification, still produced `0x62/0x10`, no verified payload and no
release completion. The difference alone is not an SG fix; production code
remains unchanged. Two small shell fixtures retain the readback and RAM A/B.

The factory visit itself was **not flash-read-only**: its background
`upgrade_mgr` launched `syn_version`, which logged a low-area erase and
firmware copy. Completion and process exit were observed before rebooting.
The preceding no-flash statements describe the earlier RAM-only phases and
do not apply to this full factory startup. Evidence and exact read commands
are in `SG_DMA_CONTRACT.md` and `out/acceptance/sg-dma/factory-20260906/`.

Final recovery returned to the original RAM FIT and verified all 33 temporary
SPA fields had reset. WAN and LAN each passed eight streams / 16 MiB with zero
bad bytes; both TX queues drained, with zero RX/TX errors, TX timeouts and
refill deficit. Probe modules were absent and WOE/debug/trap state was zero.
Evidence: `out/acceptance/sg-dma/recovery-factory-baseline/`.

The 2026-09-06 continuation now verifies complete payload DMA through
WAV600/NP1/NP2 into IDM queue 16. The working RAM fixture combines RXD swap,
SOP/EOP shift 29, unknown-SID clear, SDT104[0] = `80000010`, and the captured
factory SPA attributes. Three repeated 128-byte frames match every byte;
the first raw readback also passed IPv4/UDP checksum checks. Queue 16 is not
polled by the ordinary Ethernet driver, so its earlier packet-callback count
alone could not establish whether these payloads arrived.

The actual two-fragment test then consumed two noncontiguous 64-byte buffers
but returned `0x62/0x10` for each, without a combined frame. Release counts
remained zero. This advances complete payload proof but does not implement SG;
`NETIF_F_SG` is still off, and production code/FIT were unchanged in this
continuation. The remaining contract is a supported multi-buffer submission,
completion/ownership, and normal TX integration. Detailed controls, corrected
length-counter interpretation, and raw captures are in `SG_DMA_CONTRACT.md`.

Recovery restored the original RAM FIT and all 33 temporary SPA attributes.
WAN/LAN each passed eight streams and 16 MiB with zero bad bytes and both
TX queues fully reclaimed. Evidence: `out/acceptance/sg-dma/recovery-takeover/`.

The next continuation separates WOE DMAD last-section extraction from the
Intel SOP/EOP predicate, using a calibrated 32-byte DMAD plus independent RXD
header. Both noncontiguous 64-byte payload ranges now arrive byte-for-byte on
queue 16, but as two independent packets. Three repeated complete-frame
controls also pass. `matched=3/0 bad=0` still means no assembled second frame.
The slice counter changes, while reorder counters remain zero. This narrows
the missing contract to actual gather/routing and final source-buffer ownership;
SG remains unimplemented and disabled.

An explicit queue-ACK comparison consumes all five inspected records, reducing
pending to zero, without refilling or recycling DMA pages. No source-release
count or sampled sentinel entry changes. Reorder BA initialization interfaces
were recovered from binaries but not activated: their wireless sequence/window
configuration does not establish an skb scatterlist input or completion mapping.
The maintained changes remain in the standalone diagnostic and documentation;
production driver code, microcode files and RAM FIT were unchanged.

The final cleaned diagnostic repeated the same result and passed `W=1` and
strict checkpatch. Recovery reloaded the original RAM FIT and passed WAN/LAN
eight-stream, 16 MiB integrity checks with zero bad bytes. Both TX queues fully
completed (WAN 1,019/10,971; LAN 822/10,875); errors, timeouts and refill deficit
are zero. WOE/SSID/trap state and the 33 temporary SPA fields are clear, SPR
clearing and XPS are restored, no probe remains, and the kernel fault scan is
empty. Evidence: `out/acceptance/sg-dma/recovery-dmad-sections/`, with the actual
split/ACK capture in `out/acceptance/sg-dma/continue-separate-rxd-final.txt`.
This continuation used RAM boots only and did not run factory Linux or write
flash/saved boot state. It does not add a new checksum or performance acceptance.

The following continuation recovered and calibrated the factory PPU hardware
breakpoint/continue interface. It now reads the actual NP1 descriptor and
NP2 exception state, then restores the instruction and verifies the resumed
128-byte packet. The descriptor contains one verified payload address and
length, with no recovered scatterlist or ownership rule. Current stopped-ME
status is required because old breakpoint PCs can survive RAM reboot; the
maintained fixture excludes those stale contexts. The SSID bit-29 comparison
establishes its copy to internal control bit 24 but does not produce release
completion. A source-port-15 comparison does not establish Ethernet output.

The cleaned code passes `W=1` and strict checkpatch. Its final combined
breakpoint/split/repeat/ACK run still reports `matched=3/0 bad=0`: three exact
complete frames, two exact separate halves, no gathered frame and no source
release. SG remains unimplemented and off. Recovery on the original RAM FIT
passes WAN/LAN eight-stream, 16 MiB byte checks with both TX queues drained
(979/11,022 and 345/11,337), zero errors/timeouts/refill deficit, cleared
temporary state, no active breakpoint and no kernel fault. Evidence and the
precise implementation boundary are in
[SG_DESCRIPTOR_BREAKPOINTS.md](SG_DESCRIPTOR_BREAKPOINTS.md) and
`out/acceptance/sg-dma/recovery-breakpoints/`.

On 2026-09-07, the user clarified that sequential fragment transmission within
one Ethernet frame is sufficient; contiguous intermediate assembly is not
required. The new wire fixture independently verifies a 128-byte frame sent
by the normal netdev TX path and checks ping replies before/after capture.
WOE with corrected outbound addresses still does not deliver its full-frame
control to WAN. Thus the wire SG result is blocked by egress, not a demonstrated
failure of the MAC to preserve a frame across fragments.

The final NP1 handoff capture samples both fragments. Their eight internal
descriptor words differ only in the payload address; the control words are
identical. CPU-input comparison also shows WOE/native differences in the
destination-port operand and transmit metadata. Isolated operand replay did
not establish working egress and was removed from maintained code. No production
SG feature was enabled. Details: [SG_WIRE_BOUNDARY_TESTS.md](SG_WIRE_BOUNDARY_TESTS.md).

Final module `W=1`, strict checkpatch and Python syntax checks passed. After
the original RAM FIT was reloaded, WAN/LAN each passed eight streams / 16 MiB
with zero bad bytes; both TX queues completed all submissions (838/11,148 and
481/11,282). Temporary WOE/SSID/SPA/breakpoint state is clear, the kernel fault
scan is empty, and errors/timeouts/refill deficit are zero. Evidence:
`out/acceptance/sg-dma/recovery-wire-boundaries/`. Host addressing was preserved;
this continuation performed no flash or saved-environment writes.
