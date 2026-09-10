# Bounded Linux RXHASH integration

Latest correctness closure: [RSS_CLOSURE_ACCEPTANCE.md](RSS_CLOSURE_ACCEPTANCE.md)
records the subsequent map, port-cycle, format, RXHASH and UMEM/failure matrix.

Follow-up: `RSS_RXHASH_FIRMWARE_FALLBACK.md` records subsequent firmware-mismatch,
native fallback and valid-firmware recovery acceptance.

2026-09-09. The driver now installs the calibrated CPU-submit export program
with the two RSS programs and reports its marked L4 hash through `skb_set_hash`.
WAN and LAN conduit expose toggleable `NETIF_F_RXHASH`, enabled by default.
This is a bounded IPv4 implementation, not general IPv6/tunnel hash support.

## Changes

`zx279133-rx-hash.h` adds the 76-word PC6592 program, hooked at PC2216. Its words
were compared against the temporary module's complete live readback and are
identical. A per-program map count keeps RSS table patching out of this third
program. Firmware fingerprint gating, body-before-hook installation, readback,
and publication of `rx_hash_active` apply to all three programs together.

The receive path reads hash from descriptor metadata[2], valid bit from
metadata[3] bit31, and sets `PKT_HASH_TYPE_L4` only while RXHASH and the calibrated
programs are active. It skips all packets that ran XDP, including XDP_PASS.
It shares the existing bounded L4 header validation with RX checksum reporting;
that helper was renamed without changing its checks. Consequently IPv4 options,
fragments, nested VLANs, PPPoE and malformed lengths are not accepted for this
initial skb-hash path; ordinary IPv4 TCP/UDP with the supported Ethernet/single
VLAN layout qualify. UDP with a zero checksum is conservatively skipped too.
IPv6 receives no valid marker from the PPU. Unaccepted packets retain normal
software hash behavior. `rx_hw_hash_packets` counts actual driver annotations.

SG remains blocked and TSO remains disabled. No checksum scope was expanded.

## Evidence

All evidence is in `out/acceptance/rss/output-formats/`.

* Six WAN/LAN UDP, SYN and ACK runs each received all 128 exact frames with the
  expected full hash in the skb and corresponding marked consumed descriptors.
  The driver report counter increased by at least the fixture count; background
  traffic can contribute additional reports.
* Both RXHASH switches off: 128 packets per interface still arrived intact,
  skb hash was zero, and the combined report-counter delta was zero.
* XDP_PASS: 128 WAN packets arrived intact with zero skb hash while XDP counters
  increased. UMEM-active XDP_PASS also passed after explicit link readiness;
  after UMEM teardown/reload, 128 packets again carried the expected hash.
* RSS table f0 -> 0f triggers a hardware preparation generation increment and
  reinstall of all programs; subsequent packets retain the expected skb hash.
  The table was restored to f0.
* Same hardware queue8 mixed reuse: 1021 marked slots became unmarked after
  first-fragment traffic; 1021 unmarked slots later carried the correct new
  valid hash. This closes the stale-valid-bit gap from the preceding stage.
* WAN options, VLAN, QinQ and PPPoE traces compute the expected tuple at the
  terminal path. IPv6 extension headers take the unmarked path. The options
  fixture also proves the driver's conservative boundary: all 128 descriptors
  contain the hash but the delivered skb hash is zero, with exact raw bytes.

The raw observer uses classic socket BPF `SKF_AD_RXHASH`. In this kernel's
`net/core/filter.c` implementation, that ancillary load reads `skb->hash`
directly; it does not call a software hash generator. Each filtered packet is
then compared against the complete expected frame. This avoids treating
TPACKET's `skb_get_hash()` result as hardware proof.

## Exclusions and remaining scope

Five malformed fixtures (bad IPv4 checksum, IHL4, total-short, UDP length-short,
TCP doff4) yielded no matched PC561 context (result=-61, restored=0). They are
retained as unmatched evidence, not claimed as proven hardware drops or accepted
hash cases. A short UMEM repeat sent during link restoration received zero
fixture packets and is retained as failed `umem-pass-b.json`; it is excluded
from acceptance. The runner now requires peer reachability before injection
and retries bounded ping timeouts. The stable UMEM repeat is
`umem-confirmed.json`, followed by `umem-confirmed-after.json`.

The XSK fixture swaps only Ethernet addresses and is not a UDP echo server.
An attempted UDP echo probe timed out and is not an acceptance test. The stable
UMEM check deliberately passes traffic to the kernel rather than redirecting
it, so the helper's exit=1 for zero redirected packets is expected for that
check. This stage verifies hash suppression and restoration across UMEM setup
and teardown, not a new full AF_XDP forwarding acceptance.

Full encapsulation/IPv6 hash support and performance gain remain unclaimed.
Firmware-mismatch and hardware-failure rollback were not reinjected in this
stage; the existing common fingerprint/active gating is retained. More extensive
port/flow stress remains useful before expanding the accepted packet scope.

## Build and recovery

Built with `JOBS=8 ./port/mainline/build-zxdbg.sh`. FIT size is 11,003,260 bytes,
SHA-256 `43bef302fe172abb1c7def61cdebd319ef60fde29131d8c614af6000a9c7893d`.
Only RAM boot was used. Temporary PPU modules were unloaded before the integrated
boot. User helpers compile with `-Wall -Wextra -Werror`; the microcode header
passes strict checkpatch, Python parsing and scoped Git whitespace checks pass.
The final UDP 8192-packet and TCP 128-MiB integrity runs and board snapshot are
recorded alongside the feature tests. No flash/NAND/saveenv, host topology
change or Git commit was performed.

Run `audit-rxhash-skb.py out/acceptance/rss/output-formats` to audit the records.
All 48 checks pass. Final taint is zero, only normal DSA modules are loaded,
both peers respond to ping, and the kernel error scan is empty. Both interfaces
have RXHASH on and default 00001111 indirection tables; hash active/load counters
are 1/3 after the last UMEM cycle. IRQ masks remain 1/2. No temporary observer,
XDP or XSK process remains active.
