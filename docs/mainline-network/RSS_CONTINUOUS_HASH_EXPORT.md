# Continuous CPU-submit hash export

Follow-up: `RSS_RXHASH_SKB_INTEGRATION.md` records the subsequent production
driver integration, skb hash observations, feature toggles and lifecycle tests.

2026-09-09. The temporary RAM module `rx-ppu-output.ko` continuously computes
and exports the complete folded IPv4 tuple hash. No host single-step is used
during the traffic tests. Linux RXHASH is still disabled; the production FIT
and existing writable RSS implementation are unchanged.

## Program and terminal behavior

The module hooks PC2216 into a 76-word body at PC6592, beyond the production
RSS bodies ending at PC6499. It validates the firmware length/CRC, the complete
original hook block, and firmware padding at the cave. Both body installation
and hook writes use hardware readback. Unload restores the hook, waits for
contexts to leave, checks the cave and stop mask, and restores the saved body.

Four calibrated SPR10 branches reject non-IPv4, fragments, and protocols other
than TCP/UDP. The accepted path saves SPR31 in local word 13, preserves the CPU
destination SPR16 in SPR31, establishes header bases, and computes the hash.
It restores SPR16 and SPR31 before the seven-instruction metadata export.
The rejected path explicitly clears local word 12 bit31 and leaves word13
unchanged. SPR2 is scratch on that path; it is not reported as a valid hash.

Both paths execute the native `01100000088d3180` submit instruction followed
immediately by native `0018000000000000` END. They do not return through the
patched hook. The accepted path ends at PC6655; fallback ends at PC6667.

SPR6 is terminal scratch: normalization changes it for SYN, but it has no
subsequent use in the inserted code. The matched LAN SYN trace observes only
local words 12/13 and SPR6 changing between PC2216 and END. SPR16 and SPR31,
and all other sampled words, retain their values. The WAN first-fragment trace
branches from PC6593 directly to PC6656 and reaches END with no sampled state
change. Both traces finish with result=0, restored=0 and no stopped context.
This establishes terminal behavior on these paths; it is not a general ISA
claim about unsampled flag registers or untested output formats.

## Continuous evidence

Evidence lives in `out/acceptance/rss/continuous-output/`.

| Test | Evidence |
| --- | --- |
| WAN/LAN UDP, SYN, ACK | Six runs of 512 exact frames; every frame received unchanged, zero socket drops, one correctly hashed consumed descriptor per frame, expected queue |
| Ring reuse | Three runs of 1024 WAN UDP frames, source ports 48015/48017/48030, all queue11; three distinct hashes; third batch overwrites 1012 slots from the first with the new hash |
| IPv4 first fragment | 64 matching-length consumed descriptors on queue8, all marker bits clear |
| LAN IPv6 UDP | 64 matching-length consumed descriptors, all marker bits clear |
| Module reload | Hook/body restore=0, reinstall succeeds, live readback has zero mismatched blocks and stopped contexts |
| Normal traffic while installed | UDP 8192 packets and TCP 16 x 8 MiB, all integrity checks pass; IRQ affinity unchanged |

Fallback descriptor association uses the controlled frame length and count,
not a payload comparison. It is deliberately weaker than the raw verification
used for the six accepted formats. The tests do not yet establish marked-slot
reuse by an unmarked packet in the same hardware queue.

The first LAN attempt used a stale peer injector without `--count`; it sent no
accepted batch. The helper was synchronized and the test rerun. Two initial
1024-slot reads exceeded the serial capture window; those attempts yielded no
JSON acceptance evidence. The runner now reads at most 64 descriptors per call.
These harness failures are excluded from the passing results.

## Integration boundary

The previous blockers of continuous hash calculation, export, and relocated
submit/END are resolved for the tested basic paths. Before production RXHASH:
validate remaining header formats and submit paths, prove mixed marked/unmarked
slot reuse, account for XDP packet modification and DSA delivery, then integrate
the verified program into firmware preparation and enable skb hash reporting
with feature toggling, fallback, UMEM, reload and performance acceptance.
No SG or TSO capability is enabled by this work.

Run `audit-continuous-output.py out/acceptance/rss/continuous-output` for the
recorded checks: all 79 pass. The module builds with W=1 and passes strict checkpatch;
Python parsing and scoped whitespace checks pass.

After tests, the module is unloaded and the board is freshly RAM-booted from
the unchanged 11,001,964-byte FIT (SHA-256
`66bdbe1757b0aed66207269e1fc1afe61cb8ff1b32e6f5b47acf1c6d15de2878`).
Recovery UDP/TCP results and final board state are recorded alongside the
active-module evidence. No flash/NAND/saveenv operation, host address or route
change, or Git commit was performed.

Recovery also passes UDP 8192 packets and TCP 128 MiB. Final taint is zero,
both peers respond to ping, the kernel error scan is empty, RSS active/load
counters are 1/1 and IRQ masks remain 1/2. `receive-hashing: off [fixed]` remains
the production feature state.
