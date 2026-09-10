# RSS hash-path audit, 2026-09-07

Follow-up: [RSS_PPU_QUEUE_CONSUMER.md](RSS_PPU_QUEUE_CONSUMER.md) now resolves
the ordinary RX queue consumer and validates its effect on hardware rings.
The earlier candidate exclusions below still apply; their final consumer-search
gate is superseded by that measured trace, while the RSS hash source remains open.

The bounded factory-code audit does not establish a hardware RSS selector.
It resolves the MAC hash-mode candidate to VLAN participation in MAC lookup,
and identifies the special branch behind a tempting four-bit flow queue field.
Neither should be enabled as RSS based on its name or field width.

Evidence: [decompiled functions](../../out/acceptance/rss/hash-path-audit.json),
from the existing factory `np.ko.i64` and `plat_132.ko.i64`. Addresses below
are IDB function addresses, not running kernel virtual addresses. This is static
analysis; there were no live table/register writes for these candidates.

## MAC hash mode is a MAC-table key choice

1. `tm_hash_mode_set` (`0xe51b8`) calls `global_mac_hashmode_set` (`0xa7d64`).
   The setter reads/writes global SDT 1 entry 0, four words, and changes only
   byte 14 bit 0 (word 3 bit 16) of that response.
2. `zte_api_sw_mac_hashmode_set` (`0xfabc4`) also records the mode in
   `g_mac_hashmode` (`0x1fe7b8`). Its non-setter references are MAC entry
   add/delete paths and a getter; this software variable is not an RX queue map.
3. `zte_api_sw_mac_add_port_macaddress_hashnode` (`0xfca98`) copies its third
   argument into the MAC request's offset 12 only when this mode equals 1.
4. `tm_mactable_add_set` (`0xebaa0`) transfers that value to offset 8 of its
   internal MAC entry. `mac_table_add` (`0xab15c`) masks it to 12 bits, packs
   it above the 48-bit MAC address in an eight-byte key, and calls
   `se_hash_table_set` with SDT 9.
5. `mac_table_entry_print` (`0xaae18`) explicitly labels those upper 12 key
   bits as VLAN. Thus this API's software key construction switches between
   MAC with zero VLAN and MAC with supplied VLAN.

This gives positive evidence for MAC/VLAN lookup semantics, rather than merely
an absence of RSS symbols. The PPU's consumption of the global mode bit is not
fully decoded; this audit does not claim to have proved every silicon effect.
Changing this bit as an RSS experiment would risk altering MAC lookup behavior
without a recovered connection to CPU queue selection.

## Four-bit queue is conditional flow-action metadata

`fast_table_entry_print` (`0xa6188`) decodes response byte 31 bits 1:0 as
action. For action 1 it prints byte 28 bits 3:0 as queue; other actions use
bits 2:0. Width alone does not identify CPU RSS semantics.

`fast_hashinfo_set` (`0xa6564`) writes the ordinary three-bit queue from input
offset 170. Its four-bit assignment is guarded by `fast_tcp_droprule_check`
(`0xa4f64`), whose exact condition is input offset 168 equals 255 and input
offset 24 equals 1. That branch also writes WMM bits and a ten-bit identifier.
The adjacent special input-port 254/action-1 case has a different WMM value.

The existing mainline PPPoE local-fast path already uses action 1. Therefore
action 1 cannot be relabelled generically as CPU trap, nor may its queue nibble
be used to steer ordinary RX without tracing the relevant PPU branch and
validating packet destination. No production or diagnostic rule was installed
on the assumption that this field is a CPU queue.

## Ordinary CPU RX and remaining gap

`plat_132.ko` `cpu_net_rx` (`0xc5dc`) chooses its ring as queue + 8 * group,
then consumes that ring. The inspected function does not establish an RSS key,
indirection table, or explicit hardware-hash-to-skb contract. Some callees are
indirect and vendor skb fields are not fully typed; this is not proof that all
factory receive paths lack a hash.

The known protocol-action selector remains the only CPU queue selector with
controlled board evidence: LOOKUP_MISS queue 8/0 changed to 9/1 and back.
The new audit has not found a validated operation that combines this fixed
result with a flow hash. SE hash-table indexing and the parser's IPv6 CRC key
compression must remain separate from an RSS claim.

Next RSS-specific gate: identify the ordinary receive PPU branch that consumes
the protocol action's queue, then establish whether a tuple-derived value can
modify it while preserving WAN/LAN groups and same-flow ordering. This audit
does not decode that branch or mark RSS implemented. WAN/LAN independent NAPI
remains useful and accepted independently of that unresolved hardware contract.
