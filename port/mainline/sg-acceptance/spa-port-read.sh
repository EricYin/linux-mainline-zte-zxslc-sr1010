#!/bin/sh
# Read SPA words on an idle mainline RAM boot with no concurrent table updates.
# Factory Linux should use its locked spa_debug/port_attr_table_get interface.
set -eu

spa_wait()
{
	spa_retry=0
	while [ "$spa_retry" -lt 100 ]; do
		spa_status=$(devmem 0x19008058)
		[ $((spa_status & 1)) -ne 0 ] && return 0
		spa_retry=$((spa_retry + 1))
	done
	echo "SPA read timed out" >&2
	return 1
}

spa_gate=$(devmem 0x190000b8)
if [ $((spa_gate & 4096)) -ne 0 ]; then
	trap 'devmem 0x190000b8 32 "$spa_gate"' EXIT
	devmem 0x190000b8 32 $((spa_gate & ~4096))
fi

for spa_port in 0 5 6 10 14 15 16 17 18 19 20 21 22 23 24 25 26 27 \
		28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48; do
	for spa_word in 0 19; do
		spa_wait
		spa_cmd=$(devmem 0x19008054)
		devmem 0x19008054 32 \
			$(((spa_cmd & ~268435455) | 134217728 | (spa_port << 20) | spa_word))
		spa_wait
		spa_value=$(devmem 0x1900805c)
		printf 'SPA port=%u word=%u value=%s\n' "$spa_port" "$spa_word" "$spa_value"
	done
done
