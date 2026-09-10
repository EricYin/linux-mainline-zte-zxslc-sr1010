# Offload investigation submission — 2026-09-10

The user stopped further SG/TSO hardware exploration and requested cleanup and
local commits. The accepted checksum and RSS/multiqueue implementation is retained.
SG/TSO/TSO6 remain disabled and blocked; retained probes are diagnostics only.

## Commit scope

- Nested Linux repository: checksum trust boundaries, independent TX rings and
  RX workers/NAPI, guarded IPv4 RSS microcode and metadata, standard RSS queries
  and writable maps, lifecycle/flow protection, DSA integration and statistics.
- Outer repository: acceptance sources, SG/TSO diagnostics, evidence reports,
  initramfs IRQ-affinity preservation and the scoped AF_XDP fixture filter.
- Generated images, module/object/cache files, IDA databases, existing staged
  configuration changes and the separate router-gap report are excluded.

## Validation at submission

- AArch64 `W=1` build of `vmlinux` and modules completed successfully.
- RSS and SG/TSO external diagnostic module builds also passed with `W=1`.
- Kernel patch checkpatch: zero errors/warnings/checks; generated RX hash source
  also passed its file check. Two constant-string statistics calls were changed
  from `ethtool_sprintf` to `ethtool_puts` without changing statistic names.
- Python syntax: 52 acceptance scripts passed.
- Saved RSS closure evidence: 166 checks passed.
- Saved TSO template wire evidence: 90 checks passed.
- Saved TSO PPU trace evidence: 67 checks passed.

No new board probe, RAM boot, flash write or network test was performed during
submission. The last board recovery remains the 2026-09-09 measurement in
`out/acceptance/tso-trace/final-state.json`: UDP 8192 packets and TCP 128 MiB
verified, normal RSS restored, SG/TSO disabled. Submission-time builds were not
deployed and do not replace that measured FIT.

Raw acceptance output remains local under `out/acceptance`; these commits retain
the reproducible collectors, independent auditors and human-readable reports,
not generated capture data. See [final status](NIC_OFFLOAD_PROGRESS.md),
[RSS closure](RSS_CLOSURE_ACCEPTANCE.md) and [TSO PPU trace](TSO_PPU_CONSUMER_TRACE.md).

Nested kernel commit: `b3a74c427` (`net: zte: add RX checksum and guarded RSS multiqueue support`).
