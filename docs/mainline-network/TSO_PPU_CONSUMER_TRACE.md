# TSO template PPU consumer trace — 2026-09-09

## Result

The `idm_flag=3` template path has now been observed consuming its metadata and modifying each subsequent output header in real PPU contexts. This is hardware template repetition with per-output TCP sequence/checksum and final-length updates. It is **not accepted as general Linux TSO/TSO6**: the distinct A/B source experiment still emits A/A, source advancement has not been found, and final source-resource release remains unproven. SG, TSO and TSO6 stay disabled.

The new evidence consists of six valid traces, 505 replayed instructions, and 67 independent cross-checks. Every traced terminal header matches exactly one captured wire header. PC2054's unmatched trial and a failed serial export are retained separately and excluded from acceptance. The firmware and production RAM FIT were unchanged; breakpoints were restored before replay, and every probe was followed by a fresh RAM FIT boot.

## Observed branches and reads

Addresses below are instruction PCs in the firmware payload (big-endian 64-bit instruction at file offset `16 + PC * 8`). Packet words and SPR numbers refer to debugger snapshots, not host descriptor offsets.

| PC / instruction | Observed behavior |
| --- | --- |
| 1939 / `02903e4e80078061` | Ordinary send proceeds to 1940; flag 3 proceeds to 2044. |
| 2044 / `0706c00018e00527` | Loads packet word 28 into SPR26 and word 29 into SPR27. For two 64-byte outputs these are `00400040` and `00010000`; for 64/64/17 they are `00400011` and `00020000`. |
| 2051 / `040000dc00000fd9` | In the two-output entry trace, packet word 31 changes from `ffff0000` to `ffff0001`. This is a concrete metadata write, not a decoded release event. |
| 2053 / `0290427800056405` | The captured initial-output context proceeds to 2155 and terminates at 2172. |
| 2058–2066 | Subsequent-output contexts reload packet metadata. PC2060 repeats the word28/29 pair load. PC2064 loads word8; PC2065 loads word16. |
| 2082 | Intermediate output proceeds to 2083; final output takes 2110 to update its length. |
| 2091 / `060100201e0003ff` | Adds MSS to the working TCP sequence. Executed once in the second output, twice in the third. |
| 2094 / `02c00008082b082f` | Third-output trace returns to 2091 once; the working counter in SPR1 decreases from `00010002` to `00000002`. It then exits to 2095. This loop computes the sequence offset; it does not demonstrate payload-address advancement. |
| 2102 / `040000c00000008b` | Writes the changed TCP sequence into packet memory: IPv4 word42, IPv6 word47 in these fixtures. |
| 2106 / `0400001800000209` | Writes the changed TCP checksum: IPv4 word44, IPv6 word49. |
| 2163 / `0707c00018400527` | Reloads packet words8/9 into SPR30/31 before the common terminal sequence. |
| 2170–2172 | Each captured output executes the common terminal sequence and exits at 2172 with no stopped/busy context left. This establishes context termination; the device-side source-release semantics of these instructions remain unidentified. |

The packet29 repeat field is therefore actually read, and the sequence-offset loop is observed. The precise dispatch operation that creates the followup contexts is still not decoded. PC2058 caught two independent contexts for three outputs, whereas PC2053 caught only the initial output and PC2054 caught none. Those observations must not be collapsed into a claim that one traced context generated every frame.

## Final output length and header behavior

The input TCP sequence is `10203040`; the second and third wire sequences are `10203080` and `102030c0`.

For IPv4, the final context takes 2082→2110, then 2121→2122. PC2127 changes IP Total Length from 104 to 57 (`0068`→`0039`), and PC2128 changes the IP checksum from `a2d9` to `a308`. PC2104 also applies the shortened TCP-length contribution to the TCP checksum; PC2105 changes the output-length value in SPR20 from `00800076` to `00800047` (118→71 Ethernet bytes).

For IPv6, the final context takes 2121→2130. PC2134 changes IPv6 Payload Length from 84 to 37 (`0054`→`0025`). The final SPR20 value is `0080005b` (91 Ethernet bytes). The IPv6 trace also samples word8 changing after PC2170 from `02280000` to `016c0000`, consistent with the output-length change. Word8 is not a stable source pointer.

Both protocols emit payload lengths 64, 64, 17 with valid checksums. TCP flags remain ACK/PSH on every output. IPv4 ID remains `5401` with DF clear. These limitations from the prior wire matrix are unchanged.

## Source and completion boundary

Within each captured context, packet word9 remains unchanged. Both followup contexts in the IPv4 tail experiment use `a2cc0000`; the input source DMA address is different. Word9 is therefore described here only as an **opaque internal buffer handle/address value**, not the original source DMA address. No traced instruction is evidence of advancing that original source to the next payload fragment.

The mapped A/B experiment at PC2058 still emits A/A while the second output's sequence and checksum change correctly. This directly separates header segmentation from real payload segmentation.

In the ordinary trace, `done_before=10574`; already at PC1924 the counter is 10575 and remains so throughout the trace. The flag3 entry and followup traces likewise see `done_before + 1` throughout. A three-frame descriptor increments it only once. Consequently this counter cannot establish last-frame wire completion. It also does not, by itself, identify the point at which the last original-source DMA read completed. Sources remained pinned until reboot throughout these experiments.

## Evidence and reproduction

| Directory under `out/acceptance/tso-trace/` | Entry | Instructions | Meaning |
| --- | ---: | ---: | --- |
| `ordinary` | 1924 | 48 | Ordinary path and early descriptor completion. |
| `repeat-two` | 1924 | 42 | Flag3 branch and metadata pair consumption; A/A wire output. |
| `fork-two` | 2053 | 18 | Initial-output tail only. |
| `repeat-entry-two` | 2058 | 87 | Second-output header update; A/A persists. |
| `repeat-entry-tail-retry` | 2058 | 156 | IPv4 intermediate and final contexts (65 + 91 instructions). |
| `ipv6-repeat-entry-tail` | 2058 | 154 | IPv6 intermediate and final contexts (65 + 89 instructions). |

`child-two` did not match PC2054. Its historical top-level wire PASS is not trace acceptance. `repeat-entry-tail` captured 156 instructions in kernel memory but serial base64 export failed; it was RAM-recovered and replaced by the separately named retry. The collector now filters base64 lines, validates each chunk's decoded size, and retries incomplete chunks.

Probe implementation: `port/mainline/sg-acceptance/tso-template-probe.c` and `tso-ppu-trace.h`. The debugger replays unchanged instructions, captures up to 512 records, restores the breakpoint and clock gate, and exposes a binary debugfs trace. It only traces contexts stopped and matched at the chosen breakpoint; later contexts are not implicitly covered. Stepping changes timing, so this is not throughput or normal concurrency acceptance.

Reproduce a tail trace with `python3 port/mainline/sg-acceptance/run-tso-template.py --payload 145 --trace-steps 512 --trace-pc 2058 --directory <new-evidence-directory>`; add `--version 6` for IPv6. This stops the normal transmit path and retains mappings until the collector reloads the RAM FIT. Use one board controller at a time. Do not unload the probe or reuse its sources.

`tso-trace-audit.py` checks saved instructions against firmware, binary layout sizes, complete trace exits, early descriptor completion, per-context internal-handle stability, exact terminal-to-wire header matches, the sequence-loop counts, and existing wire checks. It does not label an unknown opcode as a DMA operation or resource release.

The external module builds with `W=1`; both C files pass strict checkpatch with zero errors, warnings or checks. No production feature bit was changed.

## Recovery and next boundary

After the final probe, the unchanged RAM FIT was reloaded and the normal DSA modules and LAN address restored. UDP sequence recovery received all 8192 packets without missing, bad, reordered or duplicate packets. All 16 TCP streams passed payload verification (128 MiB total). `final-state.json` passes all 14 recovery checks and records current IRQ/RPS masks, RSS maps/features, taint, module state, counters, firmware/FIT identity and fault scan. No capture belonging to this experiment remains. An existing `tcpdump -ni en8 -c 2 -vvv icmp and host 192.168.1.101` process (PID51578 at inspection) was recorded and left untouched; an initial overly broad no-tcpdump check was narrowed to this experiment's capture files.

This closes the bounded question of where template metadata is consumed and how subsequent/final headers are modified. General TSO/TSO6 remains blocked on a demonstrated distinct-payload fetch/advance contract, a usable source ownership/completion contract, and the already blocked SG prerequisite. Any further hardware experiment should target one of those missing contracts; merely adding Linux TSO feature bits or extending zero-payload repetition is not a valid next implementation step.
