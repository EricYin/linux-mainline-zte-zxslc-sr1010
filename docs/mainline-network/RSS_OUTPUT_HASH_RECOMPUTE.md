# CPU-submit hash recomputation calibration

Follow-up: `RSS_CONTINUOUS_HASH_EXPORT.md` records the subsequent continuous
module, relocated submit/END, and descriptor-reuse validation.

2026-09-09. Diagnostic stage only; Linux receive-hashing remains disabled.
Production RSS code and the RAM FIT are unchanged.

## Evidence

`out/acceptance/rss/output-hash/` contains six `normalized-*` fixtures for
WAN/LAN UDP, TCP SYN and TCP ACK, plus two `native-*` export fixtures.
`audit-output-hash.py` verifies 53 checks: full 32-bit hash, uniquely associated
DMA descriptor, queue, exact received bytes, stopped-context restoration and
preservation of all 128 sampled words except metadata words 12 and 13.

| Normalized fixture | Sent / received | Bad / socket drops |
| --- | --- | --- |
| WAN UDP | 666 / 666 | 0 / 0 |
| WAN SYN | 666 / 666 | 0 / 0 |
| WAN ACK | 666 / 666 | 0 / 0 |
| LAN UDP | 666 / 666 | 0 / 0 |
| LAN SYN | 652 / 652 | 0 / 0 |
| LAN ACK | 645 / 645 | 0 / 0 |

At PC2216, UDP already has the L3/L4 bases in SPR6. SYN still has SPR6=0x80.
The first unnormalized WAN SYN replay returned hash 0x3181749d instead of
0xa6993b5f, while all 792 raw frames remained intact. Its JSON is retained as
negative evidence and excluded from the passing audit. The first LAN SYN
attempt matched no context (result=-61, restored=0); its empty JSON is not
evidence. It did not leave the trace module installed.

Running the native setup pair `0601a092148000e7`, `06018090148000e7` before
the existing header-relative loads yields the correct hash for all six
fixtures. The diagnostic saves and restores SPR6 and SPR16 using host-assisted
replay. That restoration is not yet a continuous microcode implementation.

## Seven-instruction export sequence

The local-word loads are calibrated: `0707c00018600523` loads local word 12
into SPR31; `0700800018680523` loads local word 13 into SPR2.
The LAN UDP probe read 0x02001000 and 0x003e0000, respectively.

The export sequence stores SPR2 to local word 13, saves SPR31 in SPR2,
loads local word 12 into SPR31, sets only bit 31, stores it back, restores
SPR31 from SPR2, and restores the hash to SPR2 from local word 13.
It uses no host scalar writes. WAN UDP and LAN SYN replays verify that the
sequence preserves every sampled register and every other metadata word.
The native-export WAN UDP run received all 667 frames and LAN SYN all 651,
both with zero byte errors and socket drops.
The trace still executes each instruction under host control; continuous
execution, instruction flags outside the 128-word sample, and relocation of
the native submit/END pair have not been accepted.

## Remaining implementation boundary

The independent CPU-submit hash calculation is viable on these basic formats.
Before installing a continuous routine, resolve preservation or terminal use
of SPR6/SPR16 without host intervention, validate guards on other submit paths,
and prove native submit/END execution after the inserted body. Continuous
marked/unmarked descriptor reuse and format coverage must precede skb hash
reporting. Linux RXHASH toggling, DSA/XDP semantics, UMEM, engine reloads and
performance acceptance remain outstanding. SG remains blocked; TSO is off.

Validation: module W=1 build, strict checkpatch, Python parsing and the focused
audit. Recovery evidence is stored alongside the fixtures as `udp-recovery`,
`tcp-recovery` and `final` JSON. No flash/NAND write, saveenv, host topology
change or Git commit is part of this stage.

Recovery passed: UDP 8192 packets and TCP 16 x 8 MiB (128 MiB), with zero
integrity errors and unchanged IRQ affinity. Final RSS active/load counters
are 1/1, both eight-entry maps are 00001111. Taint is zero, both peers respond
to ping, and the kernel error scan is empty. FIT SHA-256 remains
`66bdbe1757b0aed66207269e1fc1afe61cb8ff1b32e6f5b47acf1c6d15de2878`.
