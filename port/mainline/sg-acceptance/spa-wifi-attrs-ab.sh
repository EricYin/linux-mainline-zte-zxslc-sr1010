#!/bin/sh
# RAM-only A/B: factory 2026-09-06 SPA word 19 for inactive Wi-Fi ports.
# Run on a fresh, idle mainline boot; reboot after the WOE probe.
set -eu
[ "${1:-}" = apply ] || { echo "usage: $0 apply" >&2; exit 2; }

spa_wait()
{
	spa_retry=0
	while [ "$spa_retry" -lt 100 ]; do
		spa_status=$(devmem 0x19008058)
		[ $((spa_status & 1)) -ne 0 ] && return 0
		spa_retry=$((spa_retry + 1))
	done
	echo "SPA access timed out" >&2
	return 1
}

spa_gate=$(devmem 0x190000b8)
if [ $((spa_gate & 4096)) -ne 0 ]; then
	trap 'devmem 0x190000b8 32 "$spa_gate"' EXIT
	devmem 0x190000b8 32 $((spa_gate & ~4096))
fi

for spa_port in 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 \
		32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48; do
	case "$spa_port" in
		16|17|18|19|27|28|29|30) spa_value=0x148c0800 ;;
		*) spa_value=0x148c0000 ;;
	esac
	spa_wait
	spa_cmd=$(devmem 0x19008054)
	spa_cmd=$(((spa_cmd & ~268435455) | (spa_port << 20) | 19))
	devmem 0x19008054 32 "$spa_cmd"
	devmem 0x1900805c 32 "$spa_value"
	spa_wait
	devmem 0x19008054 32 $((spa_cmd | 134217728))
	spa_wait
	spa_actual=$(devmem 0x1900805c)
	[ $((spa_actual)) -eq $((spa_value)) ]
	printf 'SPA port=%u word=19 verified=%s\n' "$spa_port" "$spa_actual"
done
