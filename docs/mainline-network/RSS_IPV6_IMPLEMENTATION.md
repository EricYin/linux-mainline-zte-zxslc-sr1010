# IPv6 RSS implementation index — 2026-09-12

The implementation and its supported boundaries are recorded in
[RSS_IPV6_ACCEPTANCE.md](RSS_IPV6_ACCEPTANCE.md). Basic IPv6 TCP/UDP now uses
full-address hardware tuple hashing and the existing per-interface bucket maps.
SG/TSO work remains closed.

Maintained sources:

- `linux-6.18.38/drivers/net/ethernet/zte/zx279133-rx-hash-ipv6.h`: generated PPU bodies.
- `port/mainline/rss-acceptance/generate-ipv6-rss.py`: instruction generator with output-offset guards.
- `run-ipv6-rss.py`, `run-ipv6-rss-matrix.py`, `audit-ipv6-rss.py`: independent ring, byte and skb-hash acceptance.
- `run-ipv6-streams.py`, `run-ipv6-fallback.py`, `run-ipv6-lifecycle.py`: socket integrity, unsupported layouts and lifecycle coverage.
- `rss-ram-boot.py`, `prepare-ipv6-rss.py`, `ipv6-final-state.py`: RAM deployment and final-state verification.
- `rx-ppu-trace.c`, `audit-ipv6-hash.py`, `rx-hash-observe.c`: retained calibration and diagnosis tools.

The original IPv6 implementation incorrectly recomputed a hash when a CPU
exception path discarded its L3/L4 offsets. The corrected program keeps the
hardware-selected queue but explicitly clears hash validity in that case.
The acceptance report preserves the failing observations and corrected tests.

Evidence remains local in `out/acceptance/rss/ipv6-calibration/` and
`out/acceptance/rss/ipv6-continuous/`. Follow the current acceptance report,
not the earlier factory-shell or pending-board observations, when resuming work.
