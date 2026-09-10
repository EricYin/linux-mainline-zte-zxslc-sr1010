// SPDX-License-Identifier: GPL-2.0
/*
 * RAM-only WAV600 input experiment. DMA allocations remain pinned until reboot.
 * No unload path: reboot the RAM FIT after every run, including failed probes.
 */
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/etherdevice.h>
#include <linux/if_vlan.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/ip.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/unaligned.h>
#include <net/net_namespace.h>
#include <net/page_pool/helpers.h>

#include "zx279133.h"

#define FRAME_SIZE	128
#define RING_SIZE	8
#define ARENA_SIZE	(32 * PAGE_SIZE)
#define MAGIC		"ZXSG-WAV600"

static bool arm;
static bool split;
static bool unknown_sid = true;
static bool admit_np1;
static bool trap_marker;
static bool variants;
static uint branch_marker;
static bool path_trace;
static bool rxd_swap;
static uint field_probe;
static uint sop_shift = 28;
static bool key_snapshot;
static bool ssid_probe;
static uint ssid_value;
static uint repeat = 1;
static bool separate_rxd;
static bool dmad_swap;
static bool dmad_align;
static bool ack_traps;
static uint descriptor_bp;
static bool split_bp;
static bool wire_tx;
static bool native_control;
static uint cpu_steps;
static uint cpu_control;
static bool cpu_wan_port;
static uint cpu_metadata;
static bool wire_split;
static bool cpu_full_verified;
static bool segment_flags;
static bool cpu_keep_control;
static bool ls_word1;
static uint poison_source;
static u8 *source_frame;
static dma_addr_t source_dma;
module_param(arm, bool, 0400);
module_param(split, bool, 0400);
module_param(unknown_sid, bool, 0400);
module_param(admit_np1, bool, 0400);
module_param(trap_marker, bool, 0400);
module_param(variants, bool, 0400);
module_param(branch_marker, uint, 0400);
module_param(path_trace, bool, 0400);
module_param(rxd_swap, bool, 0400);
module_param(field_probe, uint, 0400);
module_param(sop_shift, uint, 0400);
module_param(key_snapshot, bool, 0400);
module_param(ssid_probe, bool, 0400);
module_param(ssid_value, uint, 0400);
module_param(repeat, uint, 0400);
module_param(separate_rxd, bool, 0400);
module_param(dmad_swap, bool, 0400);
module_param(dmad_align, bool, 0400);
module_param(ack_traps, bool, 0400);
module_param(descriptor_bp, uint, 0400);
module_param(split_bp, bool, 0400);
module_param(wire_tx, bool, 0400);
module_param(native_control, bool, 0400);
module_param(cpu_steps, uint, 0400);
module_param(cpu_control, uint, 0400);
module_param(cpu_wan_port, bool, 0400);
module_param(cpu_metadata, uint, 0400);
module_param(wire_split, bool, 0400);
module_param(segment_flags, bool, 0400);
module_param(cpu_keep_control, bool, 0400);
module_param(ls_word1, bool, 0400);
module_param(poison_source, uint, 0400);
MODULE_PARM_DESC(arm, "Enable the RAM-only probe; reboot is mandatory afterward");
MODULE_PARM_DESC(split, "Try two fragments only after the complete frame verifies");
MODULE_PARM_DESC(unknown_sid, "Set the Intel descriptor's unknown-station flag");
MODULE_PARM_DESC(admit_np1, "Enable the factory fourth ISU weight after the baseline");
MODULE_PARM_DESC(trap_marker, "Change the NP1 trap-detail literal in RAM; reboot required");
MODULE_PARM_DESC(variants, "Compare full descriptors: SID0, SID128, EP1, OWNER_FW");
MODULE_PARM_DESC(branch_marker, "Test branch PC5177/5180/5181 using the existing detail-7 exit");
MODULE_PARM_DESC(path_trace, "Trace NP1 instruction reachability with restored diagnostic exits");
MODULE_PARM_DESC(rxd_swap, "Swap RXD words for a CPU-memory versus PCIe descriptor comparison");
MODULE_PARM_DESC(field_probe, "1: compare flags; 2: reconstruct control; 3: sample NP1 address/length registers");
MODULE_PARM_DESC(sop_shift, "28: public Intel flags; 29: measured NP1 field-position comparison");
MODULE_PARM_DESC(key_snapshot, "Compare all PPU thread keys around the complete-frame submission");
MODULE_PARM_DESC(ssid_probe, "Compare single bits of inactive SDT104 entry zero, then restore it");
MODULE_PARM_DESC(ssid_value, "Temporarily set inactive SDT104 entry zero for the payload test");
MODULE_PARM_DESC(repeat, "Number of complete frames before the split gate, from 1 to 6");
MODULE_PARM_DESC(separate_rxd, "Test a DMAD ring pointing to independent Intel RXD headers");
MODULE_PARM_DESC(dmad_swap, "Swap DMAD words independently of the RXD header");
MODULE_PARM_DESC(dmad_align, "Compensate the measured byte shift of the forwarded DMAD prefix");
MODULE_PARM_DESC(ack_traps, "Acknowledge inspected queue-16 records without recycling DMA buffers");
MODULE_PARM_DESC(descriptor_bp, "Factory breakpoints: 1 NP1 descriptor, 2 NP2 exit, 3 NP2 predicate, 4 NP1 handoff, 5 CPU input");
MODULE_PARM_DESC(split_bp, "Capture each fragment at the selected breakpoint after the full-frame control");
MODULE_PARM_DESC(wire_tx, "Address the test frame from the board to the fixed WAN peer");
MODULE_PARM_DESC(native_control, "Send one complete control frame through the normal netdev TX path");
MODULE_PARM_DESC(cpu_steps, "Trace up to 128 CPU-input instructions of the exact control frame");
MODULE_PARM_DESC(cpu_control, "CPU trace replay: 1 clears bit 20; 2 uses native TX control; 3 adds TX bit 27");
MODULE_PARM_DESC(cpu_wan_port, "Replay WAN port 6 in the traced complete WOE packet");
MODULE_PARM_DESC(cpu_metadata, "CPU packet-memory A/B mask: 1 port, 2 TX control, 4 word 31");
MODULE_PARM_DESC(wire_split, "Submit both fragments together and repair exact CPU-input test contexts");
MODULE_PARM_DESC(segment_flags, "Use separate SOP/EOP flags in the two fragment RXD records");
MODULE_PARM_DESC(cpu_keep_control, "Preserve WOE control bit 20 when adding native TX bit 27");
MODULE_PARM_DESC(ls_word1, "Use the factory MT7915 LS location: raw DMAD word 1 bit 30");
MODULE_PARM_DESC(poison_source, "Invert retained payload: 1 at matched CPU input, 2 at matched NP1 handoff");

/* Intel HOST_DSC: little-endian words, payload address in DW2, control in DW3. */
struct wav_desc {
	__le32 info0;
	__le32 info1;
	__le32 address;
	__le32 control;
	__le32 index;
};

static u8 expected[2][FRAME_SIZE];

static int invert_source_payload(void)
{
	int byte;

	if (memcmp(source_frame, expected[0], FRAME_SIZE))
		return -EINVAL;
	for (byte = 42; byte < FRAME_SIZE; byte++)
		source_frame[byte] ^= 0xff;
	dma_wmb();
	for (byte = 42; byte < FRAME_SIZE; byte++)
		if (source_frame[byte] != (u8)(expected[0][byte] ^ 0xff))
			return -EIO;
	return 0;
}

static atomic_t matched[2];
static atomic_t bad;
static u32 probe_keys[2][8][8][16];
static u32 probe_last[2][22];

static void snapshot_last(void __iomem *pps, unsigned int stage)
{
	int word;

	for (word = 0; word < ARRAY_SIZE(probe_last[stage]); word++)
		probe_last[stage][word] = readl(pps + 0x40c0 + word * 4);
}

static int snapshot_keys(void __iomem *pps, unsigned int stage)
{
	void __iomem *glb;
	u32 gate, request, ready;
	int me, thread, type, word, ret = 0;

	glb = ioremap(0x18000000, 0x100);
	if (!glb)
		return -ENOMEM;
	gate = readl(glb + 0x1c);
	writel(gate & ~BIT(3), glb + 0x1c);
	request = readl(pps + 0x4004);
	for (me = 0; me < 8; me++) {
		for (thread = 0; thread < 8; thread++) {
			u32 *key = probe_keys[stage][me][thread];

			for (type = 0; type < 3; type++) {
				/* Factory ppu_get_key_mem(), selecting each thread explicitly. */
				writel(BIT(24) | (me << 16) | (thread << 8) | type,
				       pps + 0x4004);
				ret = readl_poll_timeout_atomic(pps + 0x4008, ready,
								ready & BIT(2 - type), 1, 1000);
				if (ret)
					goto out;
				if (!type) {
					key[0] = readl(pps + 0x4080);
				} else if (type == 1) {
					for (word = 0; word < 12; word++)
						key[1 + word] = readl(pps + 0x4028 + word * 4);
				} else {
					for (word = 0; word < 3; word++)
						key[13 + word] = readl(pps + 0x4084 + word * 4);
				}
			}
		}
	}
out:
	writel(request, pps + 0x4004);
	writel(gate, glb + 0x1c);
	iounmap(glb);
	return ret;
}

static void report_keys(void)
{
	int me, thread, word, changed = 0;

	for (word = 0; word < ARRAY_SIZE(probe_last[0]); word++)
		pr_info("woe-wav: last offset=%04x %08x -> %08x\n",
			0x40c0 + word * 4, probe_last[0][word], probe_last[1][word]);
	for (me = 0; me < 8; me++) {
		for (thread = 0; thread < 8; thread++) {
			for (word = 0; word < 16; word++) {
				u32 before = probe_keys[0][me][thread][word];
				u32 after = probe_keys[1][me][thread][word];

				if (before == after)
					continue;
				changed++;
				pr_info("woe-wav: key me=%d thread=%d word=%d %08x -> %08x\n",
					me, thread, word, before, after);
			}
		}
	}
	pr_info("woe-wav: key changed_words=%d\n", changed);
}

static u16 probe_checksum(const u8 *data, unsigned int len, u32 sum)
{
	while (len >= 2) {
		sum += get_unaligned_be16(data);
		data += 2;
		len -= 2;
	}
	if (len)
		sum += *data << 8;
	while (sum >> 16)
		sum = (sum & 0xffff) + (sum >> 16);
	return ~sum;
}

static void make_frame(u8 *data, const u8 *mac, u8 seed)
{
	static const u8 source[ETH_ALEN] = { 0, 0xe0, 0x41, 0x68, 0x0d, 0x86 };
	u16 csum;
	int i;

	memset(data, 0, FRAME_SIZE);
	ether_addr_copy(data, wire_tx ? source : mac);
	ether_addr_copy(data + ETH_ALEN, wire_tx ? mac : source);
	put_unaligned_be16(ETH_P_IP, data + 12);
	data[14] = 0x45;
	put_unaligned_be16(FRAME_SIZE - ETH_HLEN, data + 16);
	put_unaligned_be16(0x5300 | seed, data + 18);
	data[22] = 64;
	data[23] = IPPROTO_UDP;
	put_unaligned_be32(wire_tx ? 0xc0a80101 : 0xc0a80164, data + 26);
	put_unaligned_be32(wire_tx ? 0xc0a80164 : 0xc0a80101, data + 30);
	put_unaligned_be16(49029, data + 34);
	put_unaligned_be16(49030, data + 36);
	put_unaligned_be16(FRAME_SIZE - 34, data + 38);
	for (i = 42; i < FRAME_SIZE; i++)
		data[i] = i * 37 + seed;
	memcpy(data + 42, MAGIC, sizeof(MAGIC) - 1);
	data[54] = seed;
	csum = probe_checksum(data + 14, 20, 0);
	put_unaligned_be16(csum, data + 24);
	csum = probe_checksum(data + 34, FRAME_SIZE - 34,
			      0xc0a8 + 0x0164 + 0xc0a8 + 0x0101 +
			      IPPROTO_UDP + FRAME_SIZE - 34);
	put_unaligned_be16(csum ? csum : 0xffff, data + 40);
}

static int probe_receive(struct sk_buff *skb, struct net_device *dev,
			 struct packet_type *pt, struct net_device *orig_dev)
{
	u8 data[FRAME_SIZE + 2 * VLAN_HLEN];
	int start, len, copied, l2 = ETH_HLEN, index;
	u16 proto;

	if (skb->pkt_type == PACKET_OUTGOING || !skb_mac_header_was_set(skb))
		goto out;
	start = skb_mac_offset(skb);
	len = skb->len - start;
	copied = min_t(int, len, sizeof(data));
	if (copied < 55 || skb_copy_bits(skb, start, data, copied))
		goto out;
	proto = get_unaligned_be16(data + 12);
	while (eth_type_vlan(htons(proto)) && l2 < ETH_HLEN + 2 * VLAN_HLEN) {
		proto = get_unaligned_be16(data + l2 + 2);
		l2 += VLAN_HLEN;
	}
	if (copied < l2 + 41 || proto != ETH_P_IP ||
	    data[l2 + 9] != IPPROTO_UDP ||
	    memcmp(data + l2 + 28, MAGIC, sizeof(MAGIC) - 1))
		goto out;
	index = data[l2 + 40] == 0x31 ? 0 : 1;
	if (len - l2 == FRAME_SIZE - ETH_HLEN &&
	    !memcmp(data, expected[index], 12) &&
	    !memcmp(data + l2, expected[index] + ETH_HLEN,
		    FRAME_SIZE - ETH_HLEN)) {
		atomic_inc(&matched[index]);
		pr_info("woe-wav: verified frame=%d dev=%s length=%d\n",
			index, dev->name, len);
	} else {
		atomic_inc(&bad);
		pr_info("woe-wav: mismatch frame=%d dev=%s length=%d\n",
			index, dev->name, len);
		print_hex_dump(KERN_INFO, "woe-wav: data ", DUMP_PREFIX_OFFSET,
			       16, 1, data, copied, false);
	}
out:
	kfree_skb(skb);
	return NET_RX_SUCCESS;
}

static struct packet_type probe_packet = {
	.type = cpu_to_be16(ETH_P_ALL),
	.func = probe_receive,
};

static int copy_trap_data(struct zx279133_eth *eth, struct zx279133_idm_desc *desc,
			  u8 *data, unsigned int length)
{
	u32 address = le32_to_cpu(READ_ONCE(desc->address));
	int slot, ret = -ENOENT;

	/* Keep the page map stable; the queue-16 record itself remains unacknowledged. */
	napi_disable(&eth->napi);
	for (slot = 0; slot < ZX279133_IDM_RX_PAGE_MAP_SIZE; slot++) {
		struct zx279133_rx_page_entry *entry = &eth->rx_page_map[slot];

		if (!entry->page || entry->key != address || entry->xsk)
			continue;
		page_pool_dma_sync_for_cpu(eth->rx_page_pool, entry->page, 0, length);
		memcpy(data, page_address(entry->page) + ZX279133_IDM_RX_PAYLOAD_OFFSET,
		       length);
		ret = 0;
		break;
	}
	napi_enable(&eth->napi);
	return ret;
}

static void dump_trap_data(struct zx279133_eth *eth, struct zx279133_idm_desc *desc)
{
	u8 data[32];

	if (!copy_trap_data(eth, desc, data, sizeof(data)))
		print_hex_dump(KERN_INFO, "woe-wav: trap data ", DUMP_PREFIX_OFFSET,
			       16, 1, data, sizeof(data), false);
}

static void verify_trap_frames(struct net_device *ndev, void __iomem *idm,
			       u32 *seen)
{
	struct zx279133_eth *eth = netdev_priv(ndev);
	struct zx279133_idm_desc *desc;
	u32 pending = readl(idm + 0xe4) & 0xffff;
	u8 data[FRAME_SIZE];
	int index;

	if (ndev->dev.parent != eth->dev || !eth->rx_descs ||
	    eth->rx_descs_dma != readl(idm + 8) || pending > ZX279133_IDM_RX_RING_SIZE)
		return;
	desc = eth->rx_descs + 16 * ZX279133_IDM_RX_RING_SIZE;
	for (; *seen < pending; (*seen)++) {
		u32 length, metadata;

		dma_rmb();
		length = le32_to_cpu(READ_ONCE(desc[*seen].length_flags)) & 0x3fff;
		metadata = le32_to_cpu(READ_ONCE(desc[*seen].metadata[0]));
		pr_info("woe-wav: queue16 record=%u length=%u metadata=%08x\n",
			*seen, length, metadata);
		pr_info("woe-wav: queue16 extra=%08x/%08x/%08x/%08x/%08x\n",
			le32_to_cpu(READ_ONCE(desc[*seen].metadata[1])),
			le32_to_cpu(READ_ONCE(desc[*seen].metadata[2])),
			le32_to_cpu(READ_ONCE(desc[*seen].metadata[3])),
			le32_to_cpu(READ_ONCE(desc[*seen].metadata[4])),
			le32_to_cpu(READ_ONCE(desc[*seen].metadata[5])));
		if (length != FRAME_SIZE) {
			if (length == 32)
				dump_trap_data(eth, &desc[*seen]);
			if (length == FRAME_SIZE / 2 &&
			    !copy_trap_data(eth, &desc[*seen], data, length)) {
				for (index = 0; index < 2; index++)
					if (!memcmp(data, expected[1] + index * length, length))
						break;
				if (index < 2)
					pr_info("woe-wav: verified separate fragment=%d bytes=%u\n",
						index, length);
				else
					atomic_inc(&bad);
			}
			continue;
		}
		if (copy_trap_data(eth, &desc[*seen], data, sizeof(data)))
			continue;
		if (poison_source == 2 && !memcmp(data, expected[0], 42)) {
			int original = 0, inverted = 0;

			for (index = 42; index < FRAME_SIZE; index++) {
				original += data[index] == expected[0][index];
				inverted += data[index] == (u8)(expected[0][index] ^ 0xff);
			}
			pr_info("woe-wav: source-result header-original=42 original=%d inverted=%d other=%d\n",
				original, inverted, FRAME_SIZE - 42 - original - inverted);
			if (inverted == FRAME_SIZE - 42)
				continue;
		}
		for (index = 0; index < ARRAY_SIZE(expected); index++) {
			if (memcmp(data, expected[index], sizeof(data)))
				continue;
			atomic_inc(&matched[index]);
			pr_info("woe-wav: verified frame=%d queue=16 length=%u\n", index, length);
			break;
		}
		if (index == ARRAY_SIZE(expected)) {
			atomic_inc(&bad);
			pr_info("woe-wav: queue16 payload mismatch\n");
		}
	}
}

static int inst_command(void __iomem *pps, u32 command)
{
	u32 value;
	int ret;

	ret = readl_poll_timeout_atomic(pps + 0xd04, value, value & 1, 1, 1000);
	if (ret)
		return ret;
	writel((readl(pps + 0xd00) & ~0x1fff) | command, pps + 0xd00);
	return readl_poll_timeout_atomic(pps + 0xd00, value, value & 1, 1, 1000);
}

/* Caller holds the PPU auto-gate open; writes are read back before returning. */
static int trace_inst_block(void __iomem *pps, u32 pc, u64 *block, bool write)
{
	u32 value;
	int i, ret;

	if (write) {
		ret = readl_poll_timeout_atomic(pps + 0xd04, value, value & 1, 1, 1000);
		if (ret)
			return ret;
		for (i = 0; i < 4; i++) {
			writel(lower_32_bits(block[i]), pps + 0xd0c + i * 8);
			writel((readl(pps + 0xd10 + i * 8) & ~0x7ffffff) |
			       upper_32_bits(block[i]), pps + 0xd10 + i * 8);
		}
		ret = inst_command(pps, pc >> 2);
		if (ret)
			return ret;
	}
	ret = inst_command(pps, BIT(12) | (pc >> 2));
	if (ret)
		return ret;
	for (i = 0; i < 4; i++) {
		u64 instruction = (u64)(readl(pps + 0xd5c + i * 8) & 0x7ffffff) << 32 |
				  readl(pps + 0xd58 + i * 8);

		if (write && block[i] != instruction)
			return -EIO;
		block[i] = instruction;
	}
	return 0;
}

static int cpu_replay_step(void __iomem *pps, u32 selected, u64 instruction, u32 next)
{
	u32 pc, status;

	writel(lower_32_bits(instruction), pps + 0xcc);
	writel(upper_32_bits(instruction), pps + 0xd0);
	writel(selected, pps + 0xc4);
	usleep_range(100, 200);
	status = readl(pps + 4);
	pc = readl(pps + 0x100 + __ffs(selected) * 4) & 0xffff;
	pr_info("woe-wav: cpu-metadata replay=%016llx pc=%u expected=%u status=%08x\n",
		instruction, pc, next, status);
	return status & selected && pc == next ? 0 : -EIO;
}

static int write_cpu_metadata(void __iomem *pps, u32 selected)
{
	static const u32 words[] = { 5, 27, 31 };
	void __iomem *pkt = pps + 0x4400 + __ffs(selected) * 0x400;
	u32 baseline[] = { readl(pkt + 20), 0x00100000, 0x00000000 };
	u32 values[] = { baseline[0] | (6 << 2), 0x08000000, 0xffff0000 };
	u64 instructions[3];
	u32 value;
	int i, op, ret;

	if (cpu_keep_control)
		values[1] |= baseline[1];
	/* Preserve length and all fields other than the observed destination port. */
	if (baseline[0] & GENMASK(7, 2))
		return -EINVAL;
	for (i = 0; i < ARRAY_SIZE(words); i++)
		if (readl(pkt + words[i] * 4) != baseline[i])
			return -EINVAL;
	for (i = 0; i < ARRAY_SIZE(words); i++) {
		if (!(cpu_metadata & BIT(i)))
			continue;
		/* Calibrated immediate halves and packet-memory word-store format. */
		instructions[0] = 0x0100c0000000852fULL | (u64)(values[i] & 0xffff) << 25;
		instructions[1] = 0x0100e0000000052fULL | (u64)(values[i] >> 16) << 25;
		instructions[2] = 0x040000180000001bULL | (u64)words[i] << 7;
		for (op = 0; op < ARRAY_SIZE(instructions); op++) {
			ret = cpu_replay_step(pps, selected, instructions[op], 1925);
			if (ret)
				return ret;
			/* Execute a checked always-branch back to the restored CPU entry. */
			ret = cpu_replay_step(pps, selected, 0x010803c303c20520ULL, 1924);
			if (ret)
				return ret;
		}
		value = readl(pkt + words[i] * 4);
		pr_info("woe-wav: cpu-metadata word=%u value=%08x expected=%08x\n",
			words[i], value, values[i]);
		if (value != values[i])
			return -EIO;
	}
	return 0;
}

static int trace_cpu_context(void __iomem *pps, u32 *mask)
{
	u64 block[4], instruction;
	u32 response[32], value;
	u32 selected = *mask, pc, step, status;
	int me, word, ret;
	void __iomem *spr, *pkt;

	me = __ffs(selected);
	spr = pps + 0x4500 + me * 0x400;
	pkt = pps + 0x4400 + me * 0x400;
	if (cpu_metadata) {
		ret = write_cpu_metadata(pps, selected);
		if (ret) {
			if (!(readl(pps + 4) & selected)) {
				*mask = 0;
			} else {
				pc = readl(pps + 0x100 + me * 4) & 0xffff;
				if (!trace_inst_block(pps, pc & ~3, block, false)) {
					writel(lower_32_bits(block[pc & 3]), pps + 0xcc);
					writel(upper_32_bits(block[pc & 3]), pps + 0xd0);
				}
			}
			return ret;
		}
	}
	for (step = 0; step <= cpu_steps; step++) {
		status = readl(pps + 4);
		if (!(status & selected)) {
			pr_info("woe-wav: cpu-trace end step=%u status=%08x busy=%08x\n",
				step, status, readl(pps + 0x18));
			*mask = 0;
			return 0;
		}
		pc = readl(pps + 0x100 + me * 4) & 0xffff;
		ret = trace_inst_block(pps, pc & ~3, block, false);
		if (ret)
			return ret;
		instruction = block[pc & 3];
		if (cpu_control && pc == 1926) {
			if (instruction != 0x0702400018d80523ULL || readl(pkt + 108) != 0x00100000)
				return -EINVAL;
			instruction = 0x010260000000052fULL;
			if (cpu_control >= 2)
				instruction |= (u64)(cpu_control == 2 ? 0x0800 : 0x0810) << 25;
			pr_info("woe-wav: cpu-trace replay control=%u pc=%u inst=%016llx\n",
				cpu_control, pc, instruction);
		}
		if (cpu_wan_port && pc == 1928) {
			if (instruction != 0x0503c01880000525ULL)
				return -EINVAL;
			instruction = 0x0103c0000c000525ULL;
			pr_info("woe-wav: cpu-trace replay WAN port=6 pc=%u\n", pc);
		}
		/* Single step leaves this replay latch unchanged; refresh it at every stop. */
		writel(lower_32_bits(instruction), pps + 0xcc);
		writel(upper_32_bits(instruction), pps + 0xd0);
		pr_info("woe-wav: cpu-trace n=%u me=%d pc=%u inst=%016llx flags=%08x\n",
			step, me, pc, instruction, readl(pps + 0x4600 + me * 0x400));
		pr_info("woe-wav: cpu-trace regs3,5,9,10,13,15,18,20=%08x/%08x/%08x/%08x/%08x/%08x/%08x/%08x pkt5,27,31=%08x/%08x/%08x\n",
			readl(spr + 12), readl(spr + 20), readl(spr + 36), readl(spr + 40),
			readl(spr + 52), readl(spr + 60), readl(spr + 72), readl(spr + 80),
			readl(pkt + 20), readl(pkt + 108), readl(pkt + 124));
		for (word = 0; word < ARRAY_SIZE(response); word++) {
			value = readl(spr + 0x80 + word * 4);
			if (!step || value != response[word])
				pr_info("woe-wav: cpu-response pc=%u word=%d value=%08x\n",
					pc, word, value);
			response[word] = value;
		}
		/* Observe the unresolved operation and final handoff without replay changes. */
		if (pc == 1981 || pc == 1982 || (pc >= 2018 && pc <= 2020)) {
			pr_info("woe-wav: cpu-output pc=%u\n", pc);
			for (word = 0; word < 32; word += 4) {
				pr_info("woe-wav: cpu-output pkt%d=%08x/%08x/%08x/%08x\n", word,
					readl(pkt + word * 4), readl(pkt + word * 4 + 4),
					readl(pkt + word * 4 + 8), readl(pkt + word * 4 + 12));
				pr_info("woe-wav: cpu-output spr%d=%08x/%08x/%08x/%08x\n", word,
					readl(spr + word * 4), readl(spr + word * 4 + 4),
					readl(spr + word * 4 + 8), readl(spr + word * 4 + 12));
				pr_info("woe-wav: cpu-output rsp%d=%08x/%08x/%08x/%08x\n", word,
					readl(spr + 0x80 + word * 4), readl(spr + 0x84 + word * 4),
					readl(spr + 0x88 + word * 4), readl(spr + 0x8c + word * 4));
			}
		}
		if (step == cpu_steps)
			break;
		writel(selected, pps + 0xc4);
		usleep_range(100, 200);
	}
	return 0;
}

static void snapshot_odma(const char *stage);

static int trace_cpu_input(void __iomem *pps, u32 *mask, u32 producer)
{
	static const char * const names[] = { "control", "joined", "head", "tail" };
	u32 selected = 0, covered = 0, resume_mask, status;
	int me, kind, word, length, ret = 0;
	int kinds[8];
	void __iomem *pkt;
	const u8 *data;

	for (me = 0; me < 8; me++) {
		if (!(*mask & BIT(me)))
			continue;
		pkt = pps + 0x4480 + me * 0x400;
		for (kind = producer == 1 ? 0 : 1; kind < (producer == 1 ? 1 : 4); kind++) {
			length = kind >= 2 ? FRAME_SIZE / 2 : FRAME_SIZE;
			data = expected[kind != 0] + (kind == 3 ? FRAME_SIZE / 2 : 0);
			for (word = 0; word < length / 4; word++)
				if (readl(pkt + word * 4) != get_unaligned_be32(data + word * 4))
					break;
			if (word != length / 4)
				continue;
			if (covered & BIT(kind))
				return -EEXIST;
			selected |= BIT(me);
			covered |= BIT(kind);
			kinds[me] = kind;
			pr_info("woe-wav: cpu-input producer=%u me=%d kind=%s bytes=%d metadata=%08x/%08x/%08x\n",
				producer, me, names[kind], length, readl(pkt - 108),
				readl(pkt - 20), readl(pkt - 4));
			pr_info("woe-wav: cpu-pointer producer=%u kind=%s word9=%08x\n",
				producer, names[kind], readl(pkt - 92));
			break;
		}
	}
	if (!selected || (producer == 1 && hweight32(selected) != 1))
		return -ENODATA;
	if (poison_source == 1) {
		ret = invert_source_payload();
		if (ret)
			return ret;
		pkt = pps + 0x4480 + __ffs(selected) * 0x400;
		for (word = 0; word < FRAME_SIZE / 4; word++)
			if (readl(pkt + word * 4) != get_unaligned_be32(expected[0] + word * 4))
				return -EIO;
		pr_info("woe-wav: source-poison bytes=%u cpu-original=%u retained=1\n",
			FRAME_SIZE - 42, FRAME_SIZE);
	}
	snapshot_odma("cpu-held");
	writel(*mask & ~selected, pps + 0xc0);
	*mask = selected;
	if (wire_split) {
		for (me = 0; me < 8; me++) {
			if (!(selected & BIT(me)))
				continue;
			ret = write_cpu_metadata(pps, BIT(me));
			pr_info("woe-wav: cpu-input producer=%u kind=%s result=%d\n",
				producer, names[kinds[me]], ret);
			if (ret)
				break;
		}
		if (ret) {
			/* Each failed/successful prelude may have stopped at a different PC. */
			for (me = 0; me < 8; me++) {
				u64 block[4];
				u32 pc;

				if (!(selected & readl(pps + 4) & BIT(me)))
					continue;
				pc = readl(pps + 0x100 + me * 4) & 0xffff;
				if (trace_inst_block(pps, pc & ~3, block, false))
					continue;
				writel(lower_32_bits(block[pc & 3]), pps + 0xcc);
				writel(upper_32_bits(block[pc & 3]), pps + 0xd0);
				writel(BIT(me), pps + 0xc0);
				readl_poll_timeout_atomic(pps + 4, status,
							  !(status & BIT(me)), 1, 1000);
			}
			*mask = 0;
			return ret;
		}
		/* All test contexts remain at PC1924; continue the entire group together. */
		writel(0x18280523, pps + 0xcc);
		writel(0x0700c000, pps + 0xd0);
		pr_info("woe-wav: cpu-input batch-ready producer=%u mask=%02x pc=1924\n",
			producer, selected);
		if (producer == 1)
			cpu_full_verified = true;
		return 0;
	}
	for (me = 0; me < 8; me++) {
		if (!(selected & BIT(me)))
			continue;
		resume_mask = BIT(me);
		ret = trace_cpu_context(pps, &resume_mask);
		/* The replay latch now contains this context's actual stopped instruction. */
		if (resume_mask) {
			int resumed;

			writel(resume_mask, pps + 0xc0);
			resumed = readl_poll_timeout_atomic(pps + 4, status,
							    !(status & resume_mask), 1, 1000);
			ret = ret ?: resumed;
		}
		*mask &= ~BIT(me);
		/* Remaining contexts are still at the original PC1924 breakpoint. */
		writel(0x18280523, pps + 0xcc);
		writel(0x0700c000, pps + 0xd0);
		pr_info("woe-wav: cpu-input producer=%u kind=%s result=%d\n",
			producer, names[kinds[me]], ret);
		if (ret)
			return ret;
	}
	if (producer == 1)
		cpu_full_verified = true;
	return 0;
}

static int probe_breakpoint(void __iomem *pps, void __iomem *woe,
			    struct net_device *ndev, u32 producer)
{
	static const u64 factory[5][4] = {
		{ 0x0700c00018400523ULL, 0, 0x0100e4004000052dULL, 0x0500c09a8000052dULL },
		{ 0x0108095a895e8520ULL, 0x010470004c000526ULL,
		  0x0108095b895e8520ULL, 0x010470000e000526ULL },
		{ 0x0018000c80000002ULL, 0x0108093d095bbfe0ULL,
		  0x0108093d895bba20ULL, 0x0108093e0966bba0ULL },
		{ 0x0018000000000000ULL, 0x0104700020000526ULL,
		  0x01080a318a348520ULL, 0x01080a320a348520ULL },
		{ 0x0700c00018280523ULL, 0x0701400018380527ULL,
		  0x0702400018d80523ULL, 0x0707800018500523ULL },
	};
	static const u32 points[] = { 5204, 4791, 4729, 5216, 1924 };
	u64 original[4], block[4];
	void __iomem *glb;
	u32 gate, mask = 0, value, halted;
	u32 pc = points[descriptor_bp - 1];
	u32 slot = pc & 3;
	int me, reg, ret, restored;

	glb = ioremap(0x18000000, 0x100);
	if (!glb)
		return -ENOMEM;
	gate = readl(glb + 0x1c);
	writel(gate & ~BIT(3), glb + 0x1c);
	ret = trace_inst_block(pps, pc & ~3, original, false);
	if (ret || memcmp(original, factory[descriptor_bp - 1], sizeof(original))) {
		ret = ret ?: -EINVAL;
		goto out;
	}
	memcpy(block, original, sizeof(block));
	/* np_ppu_test_break_point_set_store() and uTRAP0_INST_CODE_L. */
	block[slot] = 0x0080000017d80000ULL;
	writel(lower_32_bits(original[slot]), pps + 0xcc);
	writel(upper_32_bits(original[slot]), pps + 0xd0);
	ret = trace_inst_block(pps, pc & ~3, block, true);
	if (!ret) {
		pr_info("woe-wav: breakpoint submission=%u target=%u\n", producer, pc);
		if (native_control) {
			struct sk_buff *skb = netdev_alloc_skb(ndev, FRAME_SIZE);

			if (!skb) {
				ret = -ENOMEM;
				goto restore;
			}
			skb_put_data(skb, expected[0], FRAME_SIZE);
			skb_reset_mac_header(skb);
			skb_set_network_header(skb, ETH_HLEN);
			skb_set_transport_header(skb, ETH_HLEN + sizeof(struct iphdr));
			skb->protocol = htons(ETH_P_IP);
			ret = dev_queue_xmit(skb);
			if (ret)
				goto restore;
		} else {
			writel(producer, woe + 0x11e8);
		}
		msleep(20);
		halted = readl(pps + 4) & 0xff;
		pr_info("woe-wav: breakpoint flags=%08x status=%08x busy=%08x\n",
			readl(pps), halted, readl(pps + 0x18));
		for (me = 0; me < 8; me++) {
			value = readl(pps + 0x100 + me * 4);
			pr_info("woe-wav: breakpoint me=%d pc=%04x state=%08x\n",
				me, value & 0xffff, readl(pps + 0x350 + me * 4));
			if (!(halted & BIT(me)) || (value & 0xffff) != pc)
				continue;
			mask |= BIT(me);
			if (wire_split)
				continue;
			pr_info("woe-wav: breakpoint me=%d flags=%08x thread=%u\n", me,
				readl(pps + 0x4600 + me * 0x400),
				(readl(pps + 0xc8) >> (me * 4)) & 0xf);
			for (reg = 0; reg < 32; reg++)
				pr_info("woe-wav: breakpoint me=%d SPR%d=%08x\n", me, reg,
					readl(pps + 0x4500 + me * 0x400 + reg * 4));
			for (reg = 0; reg < 32; reg += 4)
				pr_info("woe-wav: breakpoint me=%d RSP%d=%08x/%08x/%08x/%08x\n",
					me, reg, readl(pps + 0x4580 + me * 0x400 + reg * 4),
					readl(pps + 0x4584 + me * 0x400 + reg * 4),
					readl(pps + 0x4588 + me * 0x400 + reg * 4),
					readl(pps + 0x458c + me * 0x400 + reg * 4));
			{
				u32 request = readl(pps + 0x4004);
				u32 thread = (readl(pps + 0xc8) >> (me * 4)) & 7;

				writel(BIT(24) | (me << 16) | (thread << 8), pps + 0x4004);
				if (!readl_poll_timeout_atomic(pps + 0x4008, value,
							       value & BIT(2), 1, 1000))
					pr_info("woe-wav: breakpoint me=%d ikey=%08x\n",
						me, readl(pps + 0x4080));
				writel(request, pps + 0x4004);
			}
			for (reg = 0; reg < 64; reg += 4)
				pr_info("woe-wav: breakpoint me=%d PKT%d=%08x/%08x/%08x/%08x\n",
					me, reg, readl(pps + 0x4400 + me * 0x400 + reg * 4),
					readl(pps + 0x4404 + me * 0x400 + reg * 4),
					readl(pps + 0x4408 + me * 0x400 + reg * 4),
					readl(pps + 0x440c + me * 0x400 + reg * 4));
		}
		if (!mask)
			ret = -ENODATA;
		if (!ret && poison_source == 2) {
			void __iomem *pkt = pps + 0x4400 + __ffs(mask) * 0x400;

			if (hweight32(mask) != 1 || producer != 1 ||
			    readl(pkt + 34 * 4) != lower_32_bits(source_dma) ||
			    readl(pkt + 35 * 4) != 0x00020000) {
				ret = -EINVAL;
			} else {
				ret = invert_source_payload();
				pr_info("woe-wav: source-poison np1 pc=%u rd=%u bytes=86 result=%d retained=1\n",
					pc, readl(woe + 0x11ec), ret);
			}
		}
	}
restore:
	restored = trace_inst_block(pps, pc & ~3, original, true);
	if (!restored && mask) {
		int resumed;

		if (cpu_steps)
			ret = trace_cpu_input(pps, &mask, producer);
		writel(mask, pps + 0xc0);
		resumed = readl_poll_timeout(pps + 0x18, value, !(value & 0xff), 10, 10000);
		ret = ret ?: resumed;
	}
	pr_info("woe-wav: breakpoint result=%d restored=%d continue=%02x\n",
		ret, restored, mask);
	ret = ret ?: restored;
out:
	writel(gate, glb + 0x1c);
	iounmap(glb);
	return ret;
}

static int field_baseline(void __iomem *pps, bool restore)
{
	static u64 saved[4];
	u64 block[4];
	void __iomem *glb;
	u32 gate;
	int ret;

	glb = ioremap(0x18000000, 0x100);
	if (!glb)
		return -ENOMEM;
	gate = readl(glb + 0x1c);
	writel(gate & ~BIT(3), glb + 0x1c);
	if (restore) {
		ret = trace_inst_block(pps, 5176, saved, true);
	} else {
		ret = trace_inst_block(pps, 5176, saved, false);
		if (!ret && saved[1] != 0x01080a1d0a333a00ULL)
			ret = -EINVAL;
		if (!ret) {
			memcpy(block, saved, sizeof(block));
			block[1] = 0x01080a1d0a330520ULL;
			ret = trace_inst_block(pps, 5176, block, true);
			if (ret)
				trace_inst_block(pps, 5176, saved, true);
		}
	}
	pr_info("woe-wav: field baseline restore=%u result=%d\n", restore, ret);
	writel(gate, glb + 0x1c);
	iounmap(glb);
	return ret;
}

static int trace_np1_path(void __iomem *pps, void __iomem *woe,
			  void __iomem *idm, struct net_device *ndev)
{
	/* Unmodified Intel instructions at PC5176..5219. */
	static const u64 factory[] = {
		0x0018000c80000002ULL, 0x01080a1d0a333a00ULL,
		0x0702800019000523ULL, 0x0702c00019180523ULL,
		0x0290a25380048401ULL, 0x0290a25f40042461ULL,
		0x0700c00018580523ULL, 0x0501005200008523ULL,
		0x0501081e00000521ULL, 0x0000000000000000ULL,
		0x0018062006860533ULL, 0x01080a220a31dfe0ULL,
		0x0104000002008521ULL, 0x0700c00018580523ULL,
		0x0504756000008525ULL, 0x05045a1e00000521ULL,
		0x0704800019100523ULL, 0x0504d4580000852dULL,
		0x0505316740008521ULL, 0x01052c0006005b81ULL,
		0x0505005000000527ULL, 0x0105260004000521ULL,
		0x01057e0000008520ULL, 0x0015c0000001001fULL,
		0x040000880000101fULL, 0x040000980000111fULL,
		0x040000a80000121fULL, 0x040000b80000131fULL,
		0x0700c00018400523ULL, 0x0000000000000000ULL,
		0x0100e4004000052dULL, 0x0500c09a8000052dULL,
		0x0000000000000000ULL, 0x040000180000041bULL,
		0x0701000018100523ULL, 0x0701400018280523ULL,
		0x01010e0356000528ULL, 0x010144001e000525ULL,
		0x040000200000011bULL, 0x040000280000029bULL,
		0x0018000000000000ULL, 0x0104700020000526ULL,
		0x01080a318a348520ULL, 0x01080a320a348520ULL,
	};
	static const u32 points[] = {
		5177, 5178, 5180, 5181, 5182, 5183, 5186, 5187,
		5188, 5194, 5195, 5204, 5209, 5214, 5216, 5217,
	};
	static const u32 sources[] = { 11, 17, 18, 19, 20 };
	struct zx279133_eth *eth = netdev_priv(ndev);
	struct zx279133_idm_desc *desc;
	void __iomem *glb;
	u64 original[4], block[4];
	u32 gate, pc, marker_pc, value, metadata;
	u32 nr_cases = field_probe == 3 ? ARRAY_SIZE(sources) * 32 :
		       (field_probe ? (field_probe == 2 ? 36 : 4) : ARRAY_SIZE(points) + 2);
	u32 observed = 0;
	u32 registers[ARRAY_SIZE(sources)] = {};
	u32 trap_count = readl(idm + 0xe4);
	int i, ret, restored;

	if (ndev->dev.parent != eth->dev || !eth->rx_descs ||
	    eth->rx_descs_dma != readl(idm + 8) || trap_count > 1)
		return -EINVAL;
	/* Queue 16 is not polled by the Ethernet driver. Never ACK these records. */
	desc = eth->rx_descs + 16 * ZX279133_IDM_RX_RING_SIZE;
	dma_rmb();
	if (trap_count) {
		metadata = le32_to_cpu(READ_ONCE(desc->metadata[0]));
		pr_info("woe-wav: trace baseline len=%08x metadata=%08x\n",
			le32_to_cpu(READ_ONCE(desc->length_flags)), metadata);
		if (metadata != 0x1062 && metadata != 0x62 && metadata != 0x762)
			return -EINVAL;
	} else {
		pr_info("woe-wav: trace baseline has no queue-16 record\n");
	}
	glb = ioremap(0x18000000, 0x100);
	if (!glb)
		return -ENOMEM;
	gate = readl(glb + 0x1c);
	if (gate & BIT(3))
		writel(gate & ~BIT(3), glb + 0x1c);
	for (i = 0; i < nr_cases; i++) {
		if (field_probe == 3)
			marker_pc = 5198;
		else if (field_probe)
			marker_pc = 5181;
		else
			marker_pc = i >= ARRAY_SIZE(points) ?
				    5180 + i - ARRAY_SIZE(points) : points[i];
		pc = marker_pc & ~3;
		ret = trace_inst_block(pps, pc, original, false);
		if (ret)
			goto out;
		if (memcmp(original, factory + (pc - 5176), sizeof(original))) {
			ret = -EINVAL;
			goto out;
		}
		memcpy(block, original, sizeof(block));
		if (field_probe == 3) {
			/* Sample each bit before the NP1 descriptor is serialized. */
			block[2] = 0x0290a25f40042461ULL;
			block[2] &= ~(GENMASK_ULL(39, 30) | GENMASK_ULL(24, 13) |
				      GENMASK_ULL(9, 5) | GENMASK_ULL(4, 0));
			block[2] |= (u64)sources[i / 32] << 35 | (u64)(i % 32) << 30 |
				    (21U << 13);
			block[3] = 0x0108000000000000ULL | (5200ULL << 31) |
				   (5217U << 15) | 0x520;
		} else if (field_probe) {
			/* Route the two outcomes to details 7 and 16, keeping diagnostics only. */
			block[1] = (block[1] & ~(GENMASK_ULL(24, 13) | GENMASK_ULL(9, 5))) |
				   (38U << 13);
			if (i < 4) {
				block[1] |= i << 5;
			} else {
				block[1] &= ~(GENMASK_ULL(34, 30) | GENMASK_ULL(4, 0));
				block[1] |= (u64)(i - 4) << 30;
			}
			block[2] = 0x0108000000000000ULL | (5183ULL << 31) |
				   (5217U << 15) | 0x520;
			pr_info("woe-wav: field case=%d instruction=%016llx\n", i, block[1]);
		} else if (i >= ARRAY_SIZE(points)) {
			/* Move the candidate displacement by five; keep other fields. */
			value = (block[marker_pc & 3] >> 13) & 0xfff;
			block[marker_pc & 3] = (block[marker_pc & 3] & ~GENMASK_ULL(24, 13)) |
					      (((value + 5) & 0xfff) << 13);
			pr_info("woe-wav: trace relative PC%u %016llx -> %016llx\n",
				marker_pc, original[marker_pc & 3], block[marker_pc & 3]);
		} else {
			/* Same always-condition and detail-7 exit as the PC5177 control. */
			block[marker_pc & 3] = 0x0108000000000000ULL |
				(u64)(marker_pc + 1) << 31 | (5222U << 15) | 0x520;
		}
		ret = trace_inst_block(pps, pc, block, true);
		if (!ret) {
			writel(i + 2, woe + 0x11e8);
			msleep(field_probe == 3 ? 20 : 200);
			ret = readl_poll_timeout(pps + 0x18, value, !(value & 0xff),
						 10, 10000);
		}
		restored = trace_inst_block(pps, pc, original, true);
		pr_info("woe-wav: trace PC%u result=%d restored=%d\n",
			marker_pc, ret, restored);
		if (ret || restored) {
			ret = ret ?: restored;
			goto out;
		}
		value = readl(idm + 0xe4);
		if (value == trap_count && !field_probe) {
			pr_info("woe-wav: trace PC%u no new queue-16 record\n", marker_pc);
			continue;
		}
		if (value != trap_count + 1) {
			ret = -EIO;
			goto out;
		}
		dma_rmb();
		metadata = le32_to_cpu(READ_ONCE(desc[trap_count].metadata[0]));
		pr_info("woe-wav: trace PC%u len=%08x metadata=%08x\n", marker_pc,
			le32_to_cpu(READ_ONCE(desc[trap_count].length_flags)),
			metadata);
		if (!field_probe)
			dump_trap_data(eth, &desc[trap_count]);
		trap_count++;
		if (field_probe == 3) {
			u32 flags = le32_to_cpu(READ_ONCE(desc[trap_count - 1].length_flags));

			if (metadata == 0x762 || (!metadata && flags == 0x20000020)) {
				registers[i / 32] |= BIT(i % 32);
			} else if (metadata != 0x1062) {
				ret = -EIO;
				goto out;
			}
		}
		if (field_probe == 2 && i >= 4) {
			if (metadata == 0x762) {
				observed |= BIT(i - 4);
			} else if (metadata != 0x1062) {
				ret = -EIO;
				goto out;
			}
		}
	}
	if (field_probe == 2)
		pr_info("woe-wav: candidate source bits=%08x\n", observed);
	if (field_probe == 3)
		for (i = 0; i < ARRAY_SIZE(sources); i++)
			pr_info("woe-wav: PC5198 source%u=%08x\n", sources[i], registers[i]);
	ret = 0;
out:
	if (gate & BIT(3))
		writel(gate, glb + 0x1c);
	iounmap(glb);
	return ret;
}

static int mark_np1_trap(void __iomem *pps)
{
	u32 high[] = { 0x00180000, 0x01047000, 0x01080a31, 0x01080a32 };
	u32 low[] = { 0x00000000, 0x20000526, 0x8a348520, 0x0a348520 };
	void __iomem *glb;
	u32 gate, value, from_high, from_low;
	u32 pc = 5216, slot = 1;
	int i, ret;

	if (!trap_marker && !branch_marker)
		return 0;
	if (branch_marker == 5177) {
		pc = 5176;
		high[0] = 0x0018000c;
		low[0] = 0x80000002;
		high[1] = 0x01080a1d;
		low[1] = 0x0a333a00;
		high[2] = 0x07028000;
		low[2] = 0x19000523;
		high[3] = 0x0702c000;
		low[3] = 0x19180523;
	} else if (branch_marker) {
		pc = 5180;
		slot = branch_marker - pc;
		high[0] = 0x0290a253;
		low[0] = 0x80048401;
		high[1] = 0x0290a25f;
		low[1] = 0x40042461;
		high[2] = 0x0700c000;
		low[2] = 0x18580523;
		high[3] = 0x05010052;
		low[3] = 0x00008523;
	}
	from_high = high[slot];
	from_low = low[slot];
	glb = ioremap(0x18000000, 0x100);
	if (!glb)
		return -ENOMEM;
	gate = readl(glb + 0x1c);
	if (gate & BIT(3))
		writel(gate & ~BIT(3), glb + 0x1c);
	ret = inst_command(pps, BIT(12) | (pc >> 2));
	if (ret)
		goto out;
	for (i = 0; i < 4; i++) {
		if (readl(pps + 0xd58 + i * 8) != low[i] ||
		    (readl(pps + 0xd5c + i * 8) & 0x7ffffff) != high[i]) {
			pr_info("woe-wav: marker rejected: PC%d differs from expected image\n",
				pc + i);
			ret = -EINVAL;
			goto out;
		}
	}
	if (branch_marker == 5177) {
		/* Keep its existing target; test the candidate always condition. */
		low[slot] = (low[slot] & ~0x7fff) | 0x520;
	} else if (branch_marker) {
		/* Candidate 0x0290 target field; reuse an existing early trap exit. */
		high[slot] = (high[slot] & ~0x1fff8) | (5222 << 3);
	} else {
		/* Proven diagnostic detail 0x10 -> 0x11. */
		low[slot] ^= BIT(25);
	}
	ret = readl_poll_timeout_atomic(pps + 0xd04, value, value & 1, 1, 1000);
	if (ret)
		goto out;
	for (i = 0; i < 4; i++) {
		writel(low[i], pps + 0xd0c + i * 8);
		writel((readl(pps + 0xd10 + i * 8) & ~0x7ffffff) | high[i],
		       pps + 0xd10 + i * 8);
	}
	ret = inst_command(pps, pc >> 2);
	if (!ret)
		ret = inst_command(pps, BIT(12) | (pc >> 2));
	if (ret)
		goto out;
	for (i = 0; i < 4; i++) {
		if (readl(pps + 0xd58 + i * 8) != low[i] ||
		    (readl(pps + 0xd5c + i * 8) & 0x7ffffff) != high[i]) {
			ret = -EIO;
			goto out;
		}
	}
	pr_info("woe-wav: PC%u %08x:%08x -> %08x:%08x verified\n",
		pc + slot, from_high, from_low, high[slot], low[slot]);
out:
	if (gate & BIT(3))
		writel(gate, glb + 0x1c);
	iounmap(glb);
	return ret;
}

static int read_se(void __iomem *se, u32 address, u32 command, u32 *data)
{
	u32 value;
	int i, ret;

	ret = readl_poll_timeout_atomic(se + 0x1c, value, value & 3, 1, 1000);
	if (ret)
		return ret;
	writel((readl(se + 8) & 0xf0000000) | address, se + 8);
	writel((readl(se + 4) & 0x7ff) | command, se + 4);
	ret = readl_poll_timeout_atomic(se + 0x1c, value, value & 3, 1, 1000);
	if (ret)
		return ret;
	for (i = 0; i < 4; i++)
		data[i] = readl(se + 0x24 + (3 - i) * 4);
	return 0;
}

static int write_eram(void __iomem *se, u32 address, const u32 *data)
{
	u32 value, actual[4];
	int i, ret;

	ret = readl_poll_timeout_atomic(se + 0x1c, value, value & 3, 1, 1000);
	if (ret)
		return ret;
	writel((readl(se + 8) & 0xf0000000) | address, se + 8);
	for (i = 0; i < 4; i++)
		writel(data[i], se + 0xc + i * 4);
	writel((readl(se + 4) & 0x7ff) | 0x8c000000, se + 4);
	ret = read_se(se, address, 0x0c000000, actual);
	return ret ?: (memcmp(data, actual, sizeof(actual)) ? -EIO : 0);
}

static int set_ssid(u32 expected_value, u32 value)
{
	void __iomem *se, *glb;
	u32 data[4], gate, address;
	int ret = -ENOMEM;

	se = ioremap(0x18048000, 0x100);
	glb = ioremap(0x18000000, 0x100);
	if (!se || !glb)
		goto unmap;
	gate = readl(glb + 0x1c);
	writel(gate & ~BIT(0), glb + 0x1c);
	ret = read_se(se, 104, 0x28000000, data);
	if (ret)
		goto out;
	if (data[0] != 0x40 || data[1] != 0x1206d7c0) {
		ret = -EINVAL;
		goto out;
	}
	address = ((data[1] >> 4) & 0xffff) << 7;
	ret = read_se(se, address, 0x0c000000, data);
	if (ret)
		goto out;
	if (data[0] != expected_value) {
		ret = -EBUSY;
		goto out;
	}
	data[0] = value;
	ret = write_eram(se, address, data);
	pr_info("woe-wav: ssid %08x -> %08x result=%d\n", expected_value, value, ret);
out:
	writel(gate, glb + 0x1c);
unmap:
	if (glb)
		iounmap(glb);
	if (se)
		iounmap(se);
	return ret;
}

static int probe_ssid(void __iomem *woe, void __iomem *idm,
		      void __iomem *pps, struct net_device *ndev)
{
	struct zx279133_eth *eth = netdev_priv(ndev);
	struct zx279133_idm_desc *desc;
	void __iomem *se, *glb;
	u32 saved[4], data[4], gate, address, pending;
	int bit, ret = -ENOMEM, restored;

	if (ndev->dev.parent != eth->dev || !eth->rx_descs ||
	    eth->rx_descs_dma != readl(idm + 8) || readl(idm + 0xe4) != 1)
		return -EINVAL;
	desc = eth->rx_descs + 16 * ZX279133_IDM_RX_RING_SIZE;
	se = ioremap(0x18048000, 0x100);
	glb = ioremap(0x18000000, 0x100);
	if (!se || !glb)
		goto unmap;
	gate = readl(glb + 0x1c);
	writel(gate & ~BIT(0), glb + 0x1c);
	ret = read_se(se, 104, 0x28000000, data);
	if (ret)
		goto out;
	if (data[0] != 0x40 || data[1] != 0x1206d7c0) {
		ret = -EINVAL;
		goto out;
	}
	address = ((data[1] >> 4) & 0xffff) << 7;
	ret = read_se(se, address, 0x0c000000, saved);
	if (ret)
		goto out;
	if (saved[0]) {
		ret = -EBUSY;
		goto out;
	}
	memcpy(data, saved, sizeof(data));
	for (bit = 0; bit < 32; bit++) {
		data[0] = BIT(bit);
		ret = write_eram(se, address, data);
		if (ret)
			break;
		writel(bit + 2, woe + 0x11e8);
		msleep(200);
		pending = readl(idm + 0xe4);
		dma_rmb();
		pr_info("woe-wav: ssid bit=%d pending=%u matched=%d bad=%d busy=%#x last=%08x/%08x\n",
			bit, pending, atomic_read(&matched[0]), atomic_read(&bad),
			readl(pps + 0x18), readl(pps + 0x40f4), readl(pps + 0x4114));
		if (pending != bit + 2) {
			ret = -ENODATA;
			break;
		}
		pr_info("woe-wav: ssid bit=%d len=%08x metadata=%08x\n", bit,
			le32_to_cpu(READ_ONCE(desc[bit + 1].length_flags)),
			le32_to_cpu(READ_ONCE(desc[bit + 1].metadata[0])));
	}
	restored = write_eram(se, address, saved);
	pr_info("woe-wav: ssid result=%d restored=%d\n", ret, restored);
	ret = ret ?: restored;
out:
	writel(gate, glb + 0x1c);
unmap:
	if (glb)
		iounmap(glb);
	if (se)
		iounmap(se);
	return ret;
}

static int snapshot_wifi_tables(void)
{
	static const u32 tables[] = { 0, 100, 101, 104 };
	void __iomem *se, *glb;
	u32 data[4], gate, base;
	int i, ret = -ENOMEM;

	se = ioremap(0x18048000, 0x100);
	glb = ioremap(0x18000000, 0x100);
	if (!se || !glb)
		goto out;
	gate = readl(glb + 0x1c);
	if ((gate & 0x1fff) == 1)
		writel(gate & ~BIT(0), glb + 0x1c);
	for (i = 0; i < ARRAY_SIZE(tables); i++) {
		ret = read_se(se, tables[i], 0x28000000, data);
		if (ret)
			break;
		pr_info("woe-wav: sdt%u node=%08x/%08x/%08x/%08x\n",
			tables[i], data[0], data[1], data[2], data[3]);
		if (!i && (data[0] != 0x4000 || data[1] != 0x16030000)) {
			ret = -EINVAL;
			break;
		}
		if (data[1] >> 28 != 1) {
			ret = -EINVAL;
			break;
		}
		base = ((data[1] >> 4) & 0xffff) << 7;
		ret = read_se(se, base, 0x0c000000, data);
		if (ret)
			break;
		pr_info("woe-wav: sdt%u entry0=%08x/%08x/%08x/%08x\n",
			tables[i], data[0], data[1], data[2], data[3]);
	}
	if ((gate & 0x1fff) == 1)
		writel(gate, glb + 0x1c);
out:
	if (se)
		iounmap(se);
	if (glb)
		iounmap(glb);
	return ret;
}

static void snapshot_odma(const char *stage)
{
	void __iomem *glb = ioremap(0x19000000, 0x100);
	void __iomem *odma = ioremap(0x19014000, 0x160);
	u32 gate;
	int pass, word;

	if (!glb || !odma)
		goto out;
	gate = readl(glb + 0xb8);
	writel(gate & ~BIT(13), glb + 0xb8);
	for (pass = 0; pass < 2; pass++) {
		pr_info("woe-wav: odma %s pass=%d counters=%08x/%08x/%08x/%08x\n",
			stage, pass, readl(odma + 0x40), readl(odma + 0x44),
			readl(odma + 0x48), readl(odma + 0x4c));
		for (word = 0x30; word <= 0x4d; word++) {
			if (word > 0x43 && word < 0x4a)
				continue;
			pr_info("woe-wav: odma %s pass=%d word=%04x value=%08x\n",
				stage, pass, 0x5000 + word, readl(odma + word * 4));
		}
	}
	writel(gate, glb + 0xb8);
out:
	if (odma)
		iounmap(odma);
	if (glb)
		iounmap(glb);
}

static void snapshot(void __iomem *woe, void __iomem *idm,
		     void __iomem *sipc, void __iomem *np1, void __iomem *pps,
		     void __iomem **release, const char *stage)
{
	int i;

	if (wire_tx) {
		snapshot_odma(stage);
		pr_info("woe-wav: idm-done %s queues=%08x/%08x/%08x/%08x\n", stage,
			readl(idm + 0x84), readl(idm + 0xac),
			readl(idm + 0xb0), readl(idm + 0xb4));
	}
	pr_info("woe-wav: %s rd=%u wr=%u enter=%u eop=%u slices=%u sipc=%u/%u\n",
		stage, readl(woe + 0x11ec), readl(woe + 0x11e8),
		readl(woe + 0x1914), readl(woe + 0x1918),
		readl(woe + 0x1948), readl(woe + 0x19c0) & 0xffff,
		readl(woe + 0x19d8) & 0xffff);
	pr_info("woe-wav: stages sipc_woe=%u ring0=%u np1_ring0=%u in=%u/%u out=%u/%u\n",
		readl(sipc + 0x48) >> 16, readl(sipc + 0x200) & 0xff,
		(readl(np1 + 0x190) >> 8) & 0xff,
		readl(np1 + 0x1f0), readl(np1 + 0x1f4),
		readl(np1 + 0x1f8), readl(np1 + 0x1fc));
	pr_info("woe-wav: sipc sch=%#x woe=%#x np2=%#x np2_err=%#x np2_len_err=%u short_drop=%u\n",
		readl(sipc + 0x88), readl(sipc + 0x104), readl(sipc + 0x1f8),
		readl(sipc + 0x1f4), readl(sipc + 0x1f0) & 0xffff,
		readl(sipc + 0x1fc) & 0xffff);
	pr_info("woe-wav: ppu busy=%#x raw_drop=%#x raw_pc=%#x mf0=%u/%u\n",
		readl(pps + 0x18), readl(pps + 0x180), readl(pps + 0x200),
		readl(pps + 0x22e0), readl(pps + 0x23a0));
	pr_info("woe-wav: mec desc=%u flow_error=%u\n",
		readl(pps + 0x9000), readl(pps + 0x9028));
	for (i = 0; i < 8; i++)
		pr_info("woe-wav: me%d mf_valid=%u/%u\n", i,
			readl(pps + 0x22e0 + i * 8),
			readl(pps + 0x23a0 + i * 8));
	pr_info("woe-wav: idm smct=%u reorder=%u/%u np2=%u len_error_ok=%#x/%#x release=%u/%u/%u\n",
		readl(idm + 0x208), readl(idm + 0x20c), readl(idm + 0x210),
		readl(idm + 0x218), readl(idm + 0x224), readl(idm + 0x228),
		readl(idm + 0x08c), readl(idm + 0x0f8), readl(idm + 0x41c));
	pr_info("woe-wav: idm cfg=%#x state=%#x axi_read=%#x axi_write=%#x\n",
		readl(idm), readl(idm + 0x204), readl(idm + 0x198),
		readl(idm + 0x19c));
	pr_info("woe-wav: idm req normal_jumbo=%#x response=%#x desc=%#x data=%#x bp=%#x/%#x\n",
		readl(idm + 0x18c), readl(idm + 0x190),
		readl(idm + 0x1e4), readl(idm + 0x1e8),
		readl(idm + 0x1ec), readl(idm + 0x1f8));
	for (i = 0; i < 3; i++)
		pr_info("woe-wav: release%d words=%08x/%08x/%08x/%08x\n", i,
			readl(release[i]), readl(release[i] + 4),
			readl(release[i] + 8), readl(release[i] + 12));
	/* Factory idm_net_poll(): 0xe4 holds queue 16/17 pending counts. */
	pr_info("woe-wav: wifi_trap_pending=%#x\n", readl(idm + 0xe4));
}

static int __init wav_probe_init(void)
{
	static const u32 release_regs[] = { 0x118, 0x11c, 0x408 };
	void __iomem *release[3] = {};
	void __iomem *woe, *idm, *sipc, *np1, *pps, *isu;
	struct net_device *ndev;
	struct wav_desc *ring;
	struct device *dev;
	dma_addr_t dma;
	void *arena;
	u32 base;
	u32 payload_address[3];
	u32 stride = separate_rxd ? 32 : sizeof(*ring);
	u32 traps_seen = 0;
	u32 ring_size = field_probe == 3 ? 256 :
			(field_probe == 2 || ssid_probe ? 64 : (path_trace ? 32 : RING_SIZE));
	u32 arena_size = field_probe == 3 ? 16 * ARENA_SIZE :
			(field_probe == 2 || ssid_probe ? 4 * ARENA_SIZE :
			 (path_trace ? 2 * ARENA_SIZE : ARENA_SIZE));
	int key_before = 0, key_after;
	bool ssid_active = false;
	int i, ret = -ENOMEM;

	if (!arm)
		return -EINVAL;
	if (sop_shift != 28 && sop_shift != 29)
		return -EINVAL;
	if (field_probe > 3)
		return -EINVAL;
	if (descriptor_bp > 5)
		return -EINVAL;
	if (poison_source > 2)
		return -EINVAL;
	if (poison_source == 1 &&
	    (native_control || split || separate_rxd || cpu_steps != 1 ||
	     descriptor_bp != 5 || cpu_metadata != 3 || repeat != 1))
		return -EINVAL;
	if (poison_source == 2 &&
	    (native_control || split || separate_rxd || cpu_steps ||
	     descriptor_bp != 4 || repeat != 1))
		return -EINVAL;
	if (cpu_steps && (cpu_steps > 128 || descriptor_bp != 5 || !wire_tx ||
			  (split && !wire_split) || repeat != 1))
		return -EINVAL;
	if (wire_split && (!split || split_bp || !separate_rxd || cpu_metadata != 3 ||
			   cpu_steps != 1))
		return -EINVAL;
	if (segment_flags && !wire_split)
		return -EINVAL;
	if (ls_word1 && !wire_split)
		return -EINVAL;
	if (cpu_keep_control && !(cpu_metadata & BIT(1)))
		return -EINVAL;
	if (cpu_control && (!cpu_steps || cpu_control > 3 || native_control))
		return -EINVAL;
	if (cpu_wan_port && (!cpu_steps || native_control))
		return -EINVAL;
	if (cpu_metadata && (!cpu_steps || cpu_metadata > 7 || native_control ||
			     cpu_control || cpu_wan_port))
		return -EINVAL;
	if (native_control && (!wire_tx || descriptor_bp != 5 || split || repeat != 1))
		return -EINVAL;
	if (split_bp && (!descriptor_bp || !split || !separate_rxd))
		return -EINVAL;
	if (descriptor_bp && (field_probe || path_trace || ssid_probe || variants ||
			      trap_marker || branch_marker || admit_np1 || key_snapshot))
		return -EINVAL;
	if (dmad_align && !separate_rxd)
		return -EINVAL;
	if (ack_traps && (field_probe || path_trace || ssid_probe || variants ||
			  trap_marker || branch_marker || admit_np1))
		return -EINVAL;
	if (separate_rxd && (field_probe || path_trace || variants || ssid_probe ||
			     trap_marker || branch_marker || admit_np1))
		return -EINVAL;
	if (!repeat || repeat > RING_SIZE - 2 ||
	    (repeat != 1 && (path_trace || field_probe || variants || ssid_probe ||
			     trap_marker || branch_marker || admit_np1)))
		return -EINVAL;
	if (ssid_probe && (field_probe || path_trace || split || variants ||
			   trap_marker || branch_marker || admit_np1 || ssid_value))
		return -EINVAL;
	if (field_probe && (path_trace || split || variants || trap_marker ||
			    branch_marker || admit_np1))
		return -EINVAL;
	if (path_trace && (split || variants || trap_marker || branch_marker || admit_np1))
		return -EINVAL;
	if (branch_marker && branch_marker != 5177 &&
	    branch_marker != 5180 && branch_marker != 5181)
		return -EINVAL;
	if (branch_marker && trap_marker)
		return -EINVAL;
	if (variants && (split || trap_marker || branch_marker || admit_np1))
		return -EINVAL;
	dev = bus_find_device_by_name(&platform_bus_type, NULL,
				      "19000000.ethernet");
	if (!dev)
		return -ENODEV;
	ndev = dev_get_by_name(&init_net, "eth0");
	if (!ndev) {
		ret = -ENODEV;
		goto put_dev;
	}
	woe = ioremap(0x19204000, 0x2000);
	idm = ioremap(0x19280000, 0x600);
	sipc = ioremap(0x19004000, 0x300);
	np1 = ioremap(0x19020000, 0x1000);
	pps = ioremap(0x18090000, 0xa000);
	isu = ioremap(0x19010000, 0x200);
	if (!woe || !idm || !sipc || !np1 || !pps || !isu)
		goto unmap;
	if (readl(woe) || readl(woe + 0x1000) || readl(woe + 0x1030) ||
	    readl(woe + 0x11ec) || readl(woe + 0x11e8) ||
	    (readl(sipc) & 3) != 3 || readl(idm + 0x4c) ||
	    readl(idm + 0x08c) || readl(idm + 0x0f8) || readl(idm + 0x41c)) {
		ret = -EBUSY;
		goto unmap;
	}
	for (i = 0; i < 3; i++) {
		base = readl(idm + release_regs[i]);
		if (base != 0x9d8a8000 + i * 0x40000) {
			ret = -EINVAL;
			goto unmap;
		}
		release[i] = ioremap_wc(base, 64);
		if (!release[i])
			goto unmap;
	}
	ret = snapshot_wifi_tables();
	if (ret)
		goto unmap;
	ret = mark_np1_trap(pps);
	if (ret)
		goto unmap;
	ret = -ENOMEM;
	arena = dma_alloc_coherent(dev, arena_size, &dma, GFP_KERNEL);
	if (!arena)
		goto unmap;
	if (upper_32_bits(dma + arena_size - 1)) {
		ret = -ERANGE;
		dma_free_coherent(dev, arena_size, arena, dma);
		goto unmap;
	}
	make_frame(expected[0], ndev->dev_addr, 0x31);
	make_frame(expected[1], ndev->dev_addr, 0x62);
	memset(arena, 0, arena_size);
	source_frame = arena + PAGE_SIZE;
	source_dma = dma + PAGE_SIZE;
	ring = arena;
	for (i = 0; i < ring_size; i++) {
		struct wav_desc *desc = arena + i * stride;
		u32 flags = 3, length = FRAME_SIZE;
		u8 *data = arena + (i + 1) * PAGE_SIZE;

		if (!variants && !path_trace && !field_probe && !ssid_probe &&
		    (i == repeat || i == repeat + 1)) {
			flags = i == repeat ? 2 : 1;
			length = FRAME_SIZE / 2;
			memcpy(data, expected[1] + (i - repeat) * length, length);
		} else {
			memcpy(data, expected[0], length);
		}
		desc->info0 = cpu_to_le32(unknown_sid ? BIT(13) : 0);
		desc->address = cpu_to_le32(lower_32_bits(dma + (i + 1) * PAGE_SIZE));
		if (field_probe == 3)
			desc->address = cpu_to_le32(lower_32_bits(dma + PAGE_SIZE));
		/* Intel MTLK_OWNER_DRV is 1, MTLK_OWNER_FW is 0. */
		desc->control = cpu_to_le32(BIT(31) |
					   ((separate_rxd && !segment_flags ? 3 : flags) <<
					    sop_shift) | length);
		desc->index = cpu_to_le32(i);
		if (i < ARRAY_SIZE(payload_address))
			payload_address[i] = le32_to_cpu(desc->address);
		if (separate_rxd) {
			struct wav_desc *rxd = arena + (ring_size + i + 1) * PAGE_SIZE;
			__le32 *dmad = (void *)desc;
			int word;

			*rxd = *desc;
			dmad[5] = cpu_to_le32(lower_32_bits(dma +
						  (ring_size + i + 1) * PAGE_SIZE));
			dmad[6] = cpu_to_le32(!!(flags & 1));
			if (i < repeat + 2)
				pr_info("woe-wav: DMAD%d RXD=%08x payload=%08x ctrl=%08x LS=%u\n",
					i, le32_to_cpu(dmad[5]), le32_to_cpu(rxd->address),
					le32_to_cpu(rxd->control), !!(flags & 1));
			if (dmad_align) {
				/* Preserve the separate SDP/LS fields. */
				for (word = 0; word < 5; word++) {
					u32 next = word < 4 ? le32_to_cpu(dmad[word + 1]) : 0;

					dmad[word] = cpu_to_le32((le32_to_cpu(dmad[word]) << 8) |
							   (next >> 24));
				}
			}
			if (ls_word1) {
				/* Set raw LS after compensating the intercepted prefix. */
				if (flags & 1)
					dmad[1] |= cpu_to_le32(BIT(30));
				if (i < repeat + 2)
					pr_info("woe-wav: DMAD%d raw-LS-word1=%08x\n",
						i, le32_to_cpu(dmad[1]));
			}
		}
		if (variants) {
			desc->info0 = cpu_to_le32(i == 1 ? 128 : 0);
			desc->info1 = cpu_to_le32(i == 2 ? BIT(8) : 0);
			if (i == 3)
				desc->control &= cpu_to_le32(~BIT(31));
			if (i < 4)
				pr_info("woe-wav: variant%d info=%08x/%08x ctrl=%08x\n", i,
					le32_to_cpu(desc->info0), le32_to_cpu(desc->info1),
					le32_to_cpu(desc->control));
		}
	}
	dma_wmb();
	dev_add_pack(&probe_packet);
	snapshot(woe, idm, sipc, np1, pps, release, "before");
	pr_info("woe-wav: ring=%pad stride=%u payload=%#x/%#x/%#x\n", &dma,
		stride, payload_address[0], payload_address[1], payload_address[2]);
	pr_info("woe-wav: unknown_sid=%u variants=%u\n",
		variants ? 0 : unknown_sid, variants);
	pr_info("woe-wav: SOP/EOP shift=%u control=%08x\n", sop_shift,
		le32_to_cpu(ring[0].control));
	if (separate_rxd) {
		/* Keep both intercepted records nonempty and aligned to 32 bytes. */
		writel((32 << 10) | (32 << 1) | 1, woe + 0x1008);
		writel((32 << 8) | 32, woe + 0x100c);
		writel(BIT(31) | (ls_word1 ? (30 << 8) | 1 : 6), woe + 0x1044);
		writel(BIT(31) | 5, woe + 0x1048);
	} else {
		/* Factory woeRxSetupWAV600: no separate DMAD. */
		writel(sizeof(*ring) << 1, woe + 0x1008);
		writel(sizeof(*ring) << 8, woe + 0x100c);
	}
	writel((readl(woe + 0x1010) & ~3) |
	       (rxd_swap ? BIT(1) : 0) | (dmad_swap ? BIT(0) : 0), woe + 0x1010);
	pr_info("woe-wav: separate-RXD=%u swap=%u cfg=%08x intercept=%08x LS=%08x SDP=%08x\n",
		separate_rxd, rxd_swap, readl(woe + 0x1008), readl(woe + 0x100c),
		readl(woe + 0x1044), readl(woe + 0x1048));
	pr_info("woe-wav: descriptor swap register=%08x\n", readl(woe + 0x1010));
	writel(readl(woe + 0x1040) & ~BIT(31), woe + 0x1040);
	writel(0, woe + 0x104c);
	writel(lower_32_bits(dma), woe + 0x12a8);
	writel(ring_size, woe + 0x1194);
	if (ssid_value) {
		ret = set_ssid(0, ssid_value);
		if (ret)
			goto stop;
		ssid_active = true;
	}
	if (field_probe == 3) {
		ret = field_baseline(pps, false);
		if (ret)
			goto stop;
	}
	if (!native_control) {
		writel(1, woe + 0x1000);
		writel(BIT(1) | BIT(2), woe);
	}
	if (key_snapshot)
		key_before = snapshot_keys(pps, 0);
	if (key_snapshot)
		snapshot_last(pps, 0);
	if (descriptor_bp) {
		ret = probe_breakpoint(pps, woe, ndev, 1);
		if (ret)
			goto stop;
	} else {
		writel(1, woe + 0x11e8);
	}
	if (key_snapshot) {
		key_after = readl_poll_timeout_atomic(idm + 0xe4, base, base, 1, 1000);
		snapshot_last(pps, 1);
		pr_info("woe-wav: first trap wait=%d pending=%#x\n", key_after, base);
	}
	if (wire_tx) {
		usleep_range(100, 200);
		snapshot(woe, idm, sipc, np1, pps, release, "early");
	}
	msleep(200);
	if (field_probe == 3) {
		ret = field_baseline(pps, true);
		if (ret)
			goto stop;
	}
	if (key_snapshot) {
		key_after = snapshot_keys(pps, 1);
		pr_info("woe-wav: key snapshot before=%d after=%d\n",
			key_before, key_after);
		if (!key_before && !key_after)
			report_keys();
	}
	snapshot(woe, idm, sipc, np1, pps, release, "single");
	if (!path_trace && !field_probe && !ssid_probe)
		verify_trap_frames(ndev, idm, &traps_seen);
	for (i = 1; i < repeat && atomic_read(&matched[0]) == i &&
	     !atomic_read(&bad); i++) {
		writel(i + 1, woe + 0x11e8);
		msleep(20);
		verify_trap_frames(ndev, idm, &traps_seen);
	}
	if (ssid_probe) {
		ret = probe_ssid(woe, idm, pps, ndev);
		pr_info("woe-wav: ssid probe result=%d\n", ret);
		snapshot(woe, idm, sipc, np1, pps, release, "ssid");
	}
	if (path_trace || field_probe) {
		ret = trace_np1_path(pps, woe, idm, ndev);
		pr_info("woe-wav: trace result=%d\n", ret);
		snapshot(woe, idm, sipc, np1, pps, release, "trace");
	}
	if (variants) {
		static const char * const stages[] = { "sid128", "ep1", "owner-fw" };

		for (i = 1; i < 4; i++) {
			writel(i + 1, woe + 0x11e8);
			msleep(20);
			snapshot(woe, idm, sipc, np1, pps, release, stages[i - 1]);
		}
	}
	if (admit_np1 && !(readl(isu + 0x40) & GENMASK(31, 24))) {
		base = readl(isu + 0x40);
		/* Factory isu_init() passes w0/w1/w2/w3 = 2/2/1/1. */
		writel(base | BIT(24), isu + 0x40);
		pr_info("woe-wav: ISU weight %#x -> %#x; no descriptor resubmission\n",
			base, readl(isu + 0x40));
		msleep(200);
		snapshot(woe, idm, sipc, np1, pps, release, "np1-admitted");
	}
	if (wire_split && cpu_full_verified) {
		/* Expose both inputs together; the peer also requires the full wire control. */
		ret = probe_breakpoint(pps, woe, ndev, repeat + 2);
		pr_info("woe-wav: wire-split result=%d\n", ret);
		msleep(200);
		snapshot(woe, idm, sipc, np1, pps, release, "split");
		verify_trap_frames(ndev, idm, &traps_seen);
	} else if (split && atomic_read(&matched[0]) == repeat && !atomic_read(&bad)) {
		if (split_bp) {
			ret = probe_breakpoint(pps, woe, ndev, repeat + 1);
			if (!ret)
				ret = probe_breakpoint(pps, woe, ndev, repeat + 2);
			if (ret)
				goto stop;
		} else {
			writel(repeat + 2, woe + 0x11e8);
		}
		msleep(200);
		snapshot(woe, idm, sipc, np1, pps, release, "split");
		verify_trap_frames(ndev, idm, &traps_seen);
	} else if (split) {
		pr_info("woe-wav: split skipped; complete-frame gate not passed\n");
	}
	if (ack_traps && traps_seen && readl(idm + 0xe4) == traps_seen) {
		/* Only release descriptors; the mapped RX pages stay pinned without refill. */
		dma_wmb();
		writel(traps_seen | (16 << 12), idm + 0x88);
		msleep(20);
		pr_info("woe-wav: queue16 acknowledged=%u remaining=%u; no buffer refill\n",
			traps_seen, readl(idm + 0xe4));
		snapshot(woe, idm, sipc, np1, pps, release, "ack");
	}
stop:
	writel(0, woe + 0x1000);
	writel(0, woe);
	readl(woe);
	msleep(20);
	dev_remove_pack(&probe_packet);
	if (ssid_active)
		pr_info("woe-wav: ssid restore=%d\n", set_ssid(ssid_value, 0));
	pr_info("woe-wav: matched=%d/%d bad=%d; buffers retained; reboot RAM FIT\n",
		atomic_read(&matched[0]), atomic_read(&matched[1]), atomic_read(&bad));
	return 0;

unmap:
	for (i = 0; i < 3; i++)
		if (release[i])
			iounmap(release[i]);
	if (np1)
		iounmap(np1);
	if (pps)
		iounmap(pps);
	if (isu)
		iounmap(isu);
	if (sipc)
		iounmap(sipc);
	if (idm)
		iounmap(idm);
	if (woe)
		iounmap(woe);
	dev_put(ndev);
put_dev:
	put_device(dev);
	return ret;
}
module_init(wav_probe_init);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("ZX279133 RAM-only WAV600 payload probe");
