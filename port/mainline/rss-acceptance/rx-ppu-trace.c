// SPDX-License-Identifier: GPL-2.0-only
/* Bounded, RAM-only ordinary RX instruction trace and selected-frame probe. */
#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/unaligned.h>

static unsigned int pc = 561;
static unsigned int steps = 512;
static int queue = -1;
static bool replayed;
static bool alu_test, calibrated;
static int calibration_result;
static bool hash_test, hash_inserted;
static bool late_hash;
static u32 hash_replay_pc = 561;
static u32 hash_tag_words, hash_raw, hash_value, hash_queue;
static u64 hash_ops[32];
static unsigned int hash_op_count;
static char *frame;
module_param(pc, uint, 0400);
module_param(steps, uint, 0400);
module_param(queue, int, 0400);
module_param(alu_test, bool, 0400);
module_param(hash_test, bool, 0400);
module_param(late_hash, bool, 0400);
MODULE_PARM_DESC(hash_test, "Compute a folded IPv4 tuple XOR on one matched context");
MODULE_PARM_DESC(alu_test, "Calibrate existing ALU forms and adjacent boolean selectors");
MODULE_PARM_DESC(queue, "Optional same-group queue override for one matched context");
module_param(frame, charp, 0400);
MODULE_PARM_DESC(frame, "Exact Ethernet frame as hex, at most 128 bytes");

struct sample {
	u64 instruction;
	u32 pc;
	u32 words[128]; /* packet words 0..63, SPR0..31, response words 0..31 */
};

static struct sample *samples;
static unsigned int count;
static int result, restored, selected_me = -1;
static u32 stopped_before, stopped_after, final_pc;
static u32 entry_mask, entry_pc[8], entry_words[8][64];
static struct dentry *directory;

static const u64 alu_templates[] = {
	0x0601e03c030001efULL, 0x060900201f0003ffULL,
	0x061500a0190001efULL, 0x062900f0320007ffULL,
	0x064900200e00009fULL, 0x0650c0180800009fULL,
	/* Candidate boolean selectors in the calibrated register-ALU format. */
	0x062100f0320007ffULL, 0x063100f0320007ffULL, 0x063900f0320007ffULL,
	/* Test operand inversion separately for the measured XNOR/AND-NOT forms. */
	0x062100f0320003ffULL, 0x062900f0320003ffULL,
};

static const u32 alu_inputs[][2] = {
	{ 0xa55a, 0x3c69 }, { 0x5aa5, 0xc396 },
	{ 0xffffffff, 0x55aa00ff }, { 0, 0 },
};

static u32 alu_results[ARRAY_SIZE(alu_templates)][ARRAY_SIZE(alu_inputs)];
static u32 alu_flags_before, alu_flags_after;
static unsigned int alu_count;
static bool branch_test;
module_param(branch_test, bool, 0400);
static bool branch_swap;
module_param(branch_swap, bool, 0400);
static const u32 branch_conditions[] = {
	0x0520, 0x2840, 0x2860, 0x2880, 0x28a0, 0x28c0, 0x2aa0,
	/* These candidates disproved a general high-bit inversion rule. */
	0x6880, 0x68a0, 0x68c0, 0x6aa0,
};

static const u32 branch_inputs[] = {
	0, BIT(2), BIT(3), BIT(4), BIT(5), BIT(6), BIT(21), 0x35, 0x55, 0x200035, ~0U,
};

static u32 branch_results[ARRAY_SIZE(branch_conditions)][ARRAY_SIZE(branch_inputs)];
static u32 branch_count;
static int branch_result;
static bool rss_map_test;
module_param(rss_map_test, bool, 0400);
static unsigned int rss_map_count;
static int rss_map_result;
static u32 rss_map_results[64];
static bool hash_export;
module_param(hash_export, bool, 0400);
static bool output_hash;
module_param(output_hash, bool, 0400);
static bool hash_exported;
static u32 exported_hash;
static bool metadata_loads;
module_param(metadata_loads, bool, 0400);
static u32 metadata_loaded[2];
static bool native_export;
module_param(native_export, bool, 0400);

static int command(void __iomem *pps, u32 cmd)
{
	u32 v;
	int ret;

	ret = readl_poll_timeout_atomic(pps + 0xd04, v, v & 1, 1, 1000);
	if (ret)
		return ret;
	writel((readl(pps + 0xd00) & ~0x1fff) | cmd, pps + 0xd00);
	return readl_poll_timeout_atomic(pps + 0xd00, v, v & 1, 1, 1000);
}

static int instruction_block(void __iomem *pps, u32 addr, u64 *block, bool write)
{
	u32 v;
	int i, ret;

	if (write) {
		ret = readl_poll_timeout_atomic(pps + 0xd04, v, v & 1, 1, 1000);
		if (ret)
			return ret;
		for (i = 0; i < 4; i++) {
			writel(lower_32_bits(block[i]), pps + 0xd0c + i * 8);
			writel((readl(pps + 0xd10 + i * 8) & ~0x7ffffff) |
			       upper_32_bits(block[i]), pps + 0xd10 + i * 8);
		}
		ret = command(pps, addr >> 2);
		if (ret)
			return ret;
	}
	ret = command(pps, BIT(12) | (addr >> 2));
	if (ret)
		return ret;
	for (i = 0; i < 4; i++) {
		u64 inst = (u64)(readl(pps + 0xd5c + i * 8) & 0x7ffffff) << 32 |
			   readl(pps + 0xd58 + i * 8);

		if (write && block[i] != inst)
			return -EIO;
		block[i] = inst;
	}
	return 0;
}

static int replay_instruction(void __iomem *pps, u32 selected, u64 inst, u32 next)
{
	writel(lower_32_bits(inst), pps + 0xcc);
	writel(upper_32_bits(inst), pps + 0xd0);
	writel(selected, pps + 0xc4);
	usleep_range(100, 200);
	if (!(readl(pps + 4) & selected) ||
	    (readl(pps + 0x100 + selected_me * 4) & 0xffff) != next)
		return -EIO;
	return 0;
}

static int replay_return(void __iomem *pps, u32 selected, u64 inst, u32 addr)
{
	u64 branch = 0x0108000000000520ULL | (u64)(addr + 2) << 31 | (u64)addr << 15;
	int ret;

	ret = replay_instruction(pps, selected, inst, addr + 1);
	if (ret)
		return ret;
	return replay_instruction(pps, selected, branch, addr);
}

static int scalar_set(void __iomem *pps, u32 selected, u32 addr, u32 reg, u32 value)
{
	u64 low = 0x010000000000852fULL | (u64)reg << 46 | (u64)(value & 0xffff) << 25;
	u64 high = 0x010020000000052fULL | (u64)reg << 46 | (u64)(value >> 16) << 25;
	int ret;

	ret = replay_return(pps, selected, low, addr);
	if (!ret)
		ret = replay_return(pps, selected, high, addr);
	if (!ret && readl(pps + 0x4500 + selected_me * 0x400 + reg * 4) != value)
		ret = -EIO;
	return ret;
}

static int calibrate_rss_map(void __iomem *pps, u32 selected, u32 addr)
{
	void __iomem *spr = pps + 0x4500 + selected_me * 0x400;
	const u32 patterns[] = { 0, ~0U, 0xa55a3c69, 0x5aa5c396 };
	u32 saved2 = readl(spr + 8), saved16 = readl(spr + 64);
	u32 saved10 = readl(spr + 40);
	u64 back = 0x0108000000000520ULL | (u64)addr << 31 | (u64)addr << 15;
	unsigned int op, input, reg, bit;
	int ret = 0, restore;

	for (op = 0; op < 4; op++) {
		reg = op == 3 ? 16 : 2;
		bit = op == 3 ? 3 : op + 4;
		for (input = 0; input < 8; input++) {
			u64 inst = 0x0108000000000000ULL |
				   (u64)(addr + 1) << 31 | (u64)(addr + 2) << 15 |
				   0x2800 | bit << 5;
			u64 copy = 0x0500000000000520ULL | 10ULL << 46 |
				   (u64)(op == 3 ? 3 : 4) << 41 |
				   (u64)reg << 35 | (op == 3 ? 19ULL << 30 : 2);
			u32 next;

			ret = scalar_set(pps, selected, addr, reg,
					 op == 3 ? input << 19 : input);
			if (!ret)
				ret = replay_return(pps, selected, copy, addr);
			if (ret)
				goto out;
			writel(lower_32_bits(inst), pps + 0xcc);
			writel(upper_32_bits(inst), pps + 0xd0);
			writel(selected, pps + 0xc4);
			usleep_range(100, 200);
			next = readl(pps + 0x100 + selected_me * 4) & 0xffff;
			if (!(readl(pps + 4) & selected) ||
			    (next != addr + 1 && next != addr + 2)) {
				ret = -EIO;
				goto out;
			}
			rss_map_results[rss_map_count++] = next == addr + 2;
			ret = replay_instruction(pps, selected, back, addr);
			if (ret)
				goto out;
		}
	}
	for (op = 0; op < ARRAY_SIZE(patterns); op++)
		for (input = 0; input < 8; input++) {
			u64 inst = 0x0104200000000522ULL | (u64)input << 25;

			ret = scalar_set(pps, selected, addr, 16, patterns[op]);
			if (!ret)
				ret = replay_return(pps, selected, inst, addr);
			if (ret)
				goto out;
			rss_map_results[rss_map_count++] = readl(spr + 64);
		}
out:
	restore = scalar_set(pps, selected, addr, 10, saved10);
	ret = ret ?: restore;
	restore = scalar_set(pps, selected, addr, 2, saved2);
	ret = ret ?: restore;
	restore = scalar_set(pps, selected, addr, 16, saved16);
	return ret ?: restore;
}

static int calibrate_alu(void __iomem *pps, u32 selected, u32 addr)
{
	void __iomem *spr = pps + 0x4500 + selected_me * 0x400;
	u32 saved[32];
	int op, input, reg, ret = 0, restore;

	for (reg = 0; reg < ARRAY_SIZE(saved); reg++)
		saved[reg] = readl(spr + reg * 4);
	alu_flags_before = readl(spr + 0x100);
	for (op = 0; op < ARRAY_SIZE(alu_templates); op++) {
		u64 inst = (alu_templates[op] & ~GENMASK_ULL(50, 19)) |
			   (3ULL << 46) | (3ULL << 35) | (8ULL << 24);

		for (input = 0; input < ARRAY_SIZE(alu_inputs); input++) {
			ret = scalar_set(pps, selected, addr, 3, alu_inputs[input][0]);
			if (!ret)
				ret = scalar_set(pps, selected, addr, 8, alu_inputs[input][1]);
			if (!ret)
				ret = replay_return(pps, selected, inst, addr);
			if (ret)
				goto restore;
			alu_results[op][input] = readl(spr + 12);
			alu_count++;
		}
	}
restore:
	for (reg = 0; reg < ARRAY_SIZE(saved); reg++) {
		if (readl(spr + reg * 4) == saved[reg])
			continue;
		restore = scalar_set(pps, selected, addr, reg, saved[reg]);
		ret = ret ?: restore;
	}
	alu_flags_after = readl(spr + 0x100);
	return ret;
}

static int calibrate_branches(void __iomem *pps, u32 selected, u32 addr)
{
	void __iomem *spr = pps + 0x4500 + selected_me * 0x400;
	u32 saved10 = readl(spr + 40), next;
	u64 back = 0x0108000000000520ULL | (u64)addr << 31 | (u64)addr << 15;
	int op, input, ret = 0, restore;

	for (op = 0; op < ARRAY_SIZE(branch_conditions); op++) {
		u64 inst = 0x0108000000000000ULL | (u64)(addr + 1 + branch_swap) << 31 |
			   (u64)(addr + 2 - branch_swap) << 15 | branch_conditions[op];

		for (input = 0; input < ARRAY_SIZE(branch_inputs); input++) {
			ret = scalar_set(pps, selected, addr, 10, branch_inputs[input]);
			if (ret)
				goto out;
			writel(lower_32_bits(inst), pps + 0xcc);
			writel(upper_32_bits(inst), pps + 0xd0);
			writel(selected, pps + 0xc4);
			usleep_range(100, 200);
			next = readl(pps + 0x100 + selected_me * 4) & 0xffff;
			if (!(readl(pps + 4) & selected) ||
			    (next != addr + 1 && next != addr + 2)) {
				ret = -EIO;
				goto out;
			}
			branch_results[op][input] = next == addr + 2;
			branch_count++;
			ret = replay_instruction(pps, selected, back, addr);
			if (ret)
				goto out;
		}
	}
out:
	restore = scalar_set(pps, selected, addr, 10, saved10);
	return ret ?: restore;
}

static u64 bit_copy(u32 dst, u32 dst_bit, u32 src, u32 src_bit, u32 width, bool clear)
{
	return 0x0500000000000520ULL | (u64)dst << 46 | (u64)dst_bit << 41 |
	       (u64)src << 35 | (u64)src_bit << 30 | (width - 1) |
	       (clear ? BIT_ULL(15) : 0);
}

static u64 packet_load(u32 dst, u32 word)
{
	return 0x0700000018000523ULL | (u64)dst << 46 | (u64)word << 19;
}

static u64 reg_xor(u32 dst, u32 a, u32 b)
{
	return 0x06200000000003ffULL | (u64)dst << 46 | (u64)a << 35 | (u64)b << 24;
}

static int hash_operation(void __iomem *pps, u32 selected, u64 inst)
{
	hash_ops[hash_op_count++] = inst;
	return replay_return(pps, selected, inst, hash_replay_pc);
}

static int compute_late_hash(void __iomem *pps, u32 selected, u32 addr)
{
	static const u32 shifts[] = { 16, 8, 4, 3 };
	void __iomem *spr = pps + 0x4500 + selected_me * 0x400;
	u32 saved6 = readl(spr + 24), saved16 = readl(spr + 64);
	u64 ops[] = {
		0x0700800028180523ULL, 0x0704000028200523ULL, reg_xor(2, 2, 16),
		0x0704000008000523ULL, reg_xor(2, 2, 16),
		0x0704000028120520ULL, bit_copy(16, 0, 16, 0, 8, true), reg_xor(2, 2, 16),
	};
	int i, ret;

	if (saved16 && addr != 2216)
		return -EINVAL;
	hash_replay_pc = addr;
	if (addr == 1893 || addr == 2216) {
		if (addr == 1893 && saved6 != ((readl(spr + 80) >> 16) & 0xff))
			return -EINVAL;
		ret = hash_operation(pps, selected, 0x0601a092148000e7ULL);
		if (!ret)
			ret = hash_operation(pps, selected, 0x06018090148000e7ULL);
		if (ret)
			return ret;
	}
	for (i = 0; i < ARRAY_SIZE(ops); i++) {
		ret = hash_operation(pps, selected, ops[i]);
		if (ret)
			return ret;
	}
	hash_raw = readl(spr + 8);
	for (i = 0; i < ARRAY_SIZE(shifts); i++) {
		u64 move = bit_copy(16, 0, 2, shifts[i], 32 - shifts[i], true);

		ret = hash_operation(pps, selected, move);
		if (!ret)
			ret = hash_operation(pps, selected, reg_xor(2, 2, 16));
		if (ret)
			return ret;
	}
	hash_value = readl(spr + 8);
	if (addr == 1893) {
		ret = hash_operation(pps, selected, bit_copy(6, 0, 20, 16, 8, true));
		if (ret || readl(spr + 24) != saved6)
			return ret ?: -EIO;
	}
	if (addr == 2216) {
		ret = scalar_set(pps, selected, addr, 6, saved6);
		return ret ?: scalar_set(pps, selected, addr, 16, saved16);
	}
	return hash_operation(pps, selected, 0x010400000000852fULL);
}

static int export_hash(void __iomem *pps, u32 selected, u32 addr)
{
	u64 ops[] = {
		0x040000100000069bULL, /* SPR2 to local word 13. */
		bit_copy(2, 0, 31, 0, 32, true),
		0x0707c00018600523ULL, /* Local word 12 to SPR31. */
		0x0100000000000520ULL | 31ULL << 46 | 31ULL << 41 | BIT_ULL(25),
		0x040000f80000061bULL, /* Marked flags to local word 12. */
		bit_copy(31, 0, 2, 0, 32, true),
		0x0700800018680523ULL, /* Restore hash from local word 13. */
	};
	int i, ret;

	for (i = 0; i < ARRAY_SIZE(ops); i++) {
		ret = replay_return(pps, selected, ops[i], addr);
		if (ret)
			return ret;
	}
	return 0;
}

static int compute_tuple_hash(void __iomem *pps, u32 selected)
{
	static const u32 shifts[] = { 16, 8, 4, 3 };
	void __iomem *spr = pps + 0x4500 + selected_me * 0x400;
	u32 saved3 = readl(spr + 12), saved8 = readl(spr + 32);
	u64 ops[] = {
		packet_load(2, 14), packet_load(3, 15), reg_xor(2, 2, 3),
		packet_load(3, 40 + hash_tag_words), bit_copy(8, 16, 3, 0, 16, true),
		packet_load(3, 41 + hash_tag_words), bit_copy(8, 0, 3, 16, 16, false),
		reg_xor(2, 2, 8), packet_load(3, 37 + hash_tag_words),
		bit_copy(8, 0, 3, 0, 8, true), reg_xor(2, 2, 8),
	};
	int i, ret = 0, restore;

	for (i = 0; i < ARRAY_SIZE(ops); i++) {
		ret = hash_operation(pps, selected, ops[i]);
		if (ret)
			goto out;
	}
	hash_raw = readl(spr + 8);
	for (i = 0; i < ARRAY_SIZE(shifts); i++) {
		u64 move = bit_copy(3, 0, 2, shifts[i], 32 - shifts[i], true);

		ret = hash_operation(pps, selected, move);
		if (!ret)
			ret = hash_operation(pps, selected, reg_xor(2, 2, 3));
		if (ret)
			goto out;
	}
	hash_value = readl(spr + 8);
out:
	restore = scalar_set(pps, selected, 561, 3, saved3);
	ret = ret ?: restore;
	restore = scalar_set(pps, selected, 561, 8, saved8);
	return ret ?: restore;
}

static int capture(void __iomem *pps, u32 selected)
{
	u64 block[4];
	u32 addr;
	int i, ret;

	for (count = 0; count < steps; count++) {
		struct sample *s = &samples[count];

		if (!(readl(pps + 4) & selected))
			return 0;
		addr = readl(pps + 0x100 + selected_me * 4) & 0xffff;
		final_pc = addr;
		if (addr >= 12288)
			return -ERANGE;
		ret = instruction_block(pps, addr & ~3, block, false);
		if (ret)
			return ret;
		s->pc = addr;
		s->instruction = block[addr & 3];
		for (i = 0; i < ARRAY_SIZE(s->words); i++)
			s->words[i] = readl(pps + 0x4400 + selected_me * 0x400 + i * 4);
		if (hash_export && !hash_exported && addr == 2216 &&
		    (s->words[74] & BIT(4)) && !(s->words[74] & BIT(21)) &&
		    (s->words[74] & (BIT(5) | BIT(6)))) {
			u32 saved31 = s->words[95];
			int restore;

			exported_hash = s->words[66];
			if (output_hash) {
				ret = compute_late_hash(pps, selected, addr);
				if (ret)
					return ret;
				exported_hash = hash_value;
			}
			if (metadata_loads) {
				for (i = 0; i < 2; i++) {
					u64 load = 0x0707c00018000523ULL |
						   (u64)(12 + i) << 19;

					ret = replay_return(pps, selected, load, addr);
					if (ret)
						return ret;
					metadata_loaded[i] = readl(pps + 0x4500 +
								 selected_me * 0x400 + 124);
				}
				ret = scalar_set(pps, selected, addr, 31, saved31);
				if (ret)
					return ret;
			}
			if (native_export) {
				ret = export_hash(pps, selected, addr);
				restore = 0;
			} else {
				ret = replay_return(pps, selected, 0x040000100000069bULL, addr);
				if (!ret)
					ret = scalar_set(pps, selected, addr, 31,
							 s->words[12] | BIT(31));
				if (!ret)
					ret = replay_return(pps, selected,
							    0x040000f80000061bULL, addr);
				restore = scalar_set(pps, selected, addr, 31, saved31);
			}
			if (ret || restore)
				return ret ?: restore;
			hash_exported = true;
		}
		if (rss_map_test && !rss_map_count && (addr == 1558 || addr == 1893)) {
			rss_map_result = calibrate_rss_map(pps, selected, addr);
			if (rss_map_result)
				return rss_map_result;
		}
		if (branch_test && !branch_count && (addr == 1558 || addr == 1893)) {
			branch_result = calibrate_branches(pps, selected, addr);
			if (branch_result)
				return branch_result;
		}
		if (hash_test && !late_hash && !count) {
			ret = compute_tuple_hash(pps, selected);
			if (ret)
				return ret;
		}
		if (alu_test && !calibrated && (addr == 1558 || addr == 1893)) {
			calibrated = true;
			calibration_result = calibrate_alu(pps, selected, addr);
			if (calibration_result)
				return calibration_result;
		}
		if (queue >= 0 && (addr == 1558 || addr == 1893)) {
			unsigned int native_queue = (s->words[96] >> 26) & 15;

			if (replayed || s->instruction != 0x0504210680000523ULL ||
			    (native_queue >> 3) != (queue >> 3) || (s->words[80] & 0xffff))
				return -EINVAL;
			/* Calibrated upper-immediate form; preserve SPR16's zero low half. */
			s->instruction = 0x010420000000052fULL | (u64)queue << 25;
			replayed = true;
		}
		if (hash_test && (addr == 1558 || addr == 1893)) {
			u32 native_queue = (s->words[96] >> 26) & 15;

			if (late_hash) {
				ret = compute_late_hash(pps, selected, addr);
				if (ret)
					return ret;
			}
			if (hash_inserted || s->instruction != 0x0504210680000523ULL ||
			    (!late_hash && s->words[66] != hash_value))
				return -EINVAL;
			/* Keep the direction bit from the real protocol response. */
			ret = replay_return(pps, selected, s->instruction, addr);
			if (ret)
				return ret;
			s->instruction = bit_copy(16, 16, 2, 0, 3, false);
			hash_queue = (native_queue & 8) | (hash_value & 7);
			hash_inserted = true;
		}
		/* The replay latch is not automatically updated by single-step. */
		writel(lower_32_bits(s->instruction), pps + 0xcc);
		writel(upper_32_bits(s->instruction), pps + 0xd0);
		writel(selected, pps + 0xc4);
		usleep_range(100, 200);
	}
	return readl(pps + 4) & selected ? -EAGAIN : 0;
}

static int run_trace(const struct firmware *fw, const u8 *expected, size_t len)
{
	void __iomem *pps, *glb;
	u64 original[4], block[4];
	u32 gate, mask = 0, selected = 0, v;
	int i, me, ret;

	pps = ioremap(0x18090000, 0xa000);
	glb = ioremap(0x18000000, 0x100);
	if (!pps || !glb) {
		ret = -ENOMEM;
		goto unmap;
	}
	stopped_before = readl(pps + 4) & 0xff;
	if (stopped_before) {
		ret = -EBUSY;
		goto unmap;
	}
	gate = readl(glb + 0x1c);
	writel(gate & ~BIT(3), glb + 0x1c);
	ret = instruction_block(pps, pc & ~3, original, false);
	if (ret)
		goto gate_out;
	for (i = 0; i < 4; i++) {
		if (original[i] != get_unaligned_be64(fw->data + 16 + ((pc & ~3) + i) * 8)) {
			ret = -EINVAL;
			goto gate_out;
		}
	}
	memcpy(block, original, sizeof(block));
	block[pc & 3] = 0x0080000017d80000ULL;
	ret = instruction_block(pps, pc & ~3, block, true);
	if (!ret) {
		/* Host sends the exact fixture repeatedly across this bounded window. */
		msleep(20);
		mask = readl(pps + 4) & 0xff;
		entry_mask = mask;
		for (me = 0; me < 8; me++) {
			entry_pc[me] = readl(pps + 0x100 + me * 4) & 0xffff;
			if (mask & BIT(me))
				for (i = 0; i < 64; i++)
					entry_words[me][i] = readl(pps + 0x4400 +
								 me * 0x400 + i * 4);
			if (!(mask & BIT(me)) ||
			    (readl(pps + 0x100 + me * 4) & 0xffff) != pc)
				continue;
			for (i = 0; i < len / 4; i++)
				if (readl(pps + 0x4480 + me * 0x400 + i * 4) !=
				    get_unaligned_be32(expected + i * 4))
					break;
			if (i == len / 4 && !selected) {
				selected = BIT(me);
				selected_me = me;
			}
		}
		ret = selected ? 0 : -ENODATA;
	}
	restored = instruction_block(pps, pc & ~3, original, true);
	if (!restored) {
		/* Include contexts that reached the breakpoint during restoration. */
		mask = readl(pps + 4) & 0xff;
		for (me = 0; me < 8; me++)
			if ((readl(pps + 0x100 + me * 4) & 0xffff) != pc)
				mask &= ~BIT(me);
		/* Resume other contexts at the restored entry before tracing one. */
		writel(lower_32_bits(original[pc & 3]), pps + 0xcc);
		writel(upper_32_bits(original[pc & 3]), pps + 0xd0);
		writel(mask & ~selected, pps + 0xc0);
		usleep_range(1000, 2000);
		if (selected)
			ret = capture(pps, selected);
		if (selected && (readl(pps + 4) & selected)) {
			v = readl(pps + 0x100 + selected_me * 4) & 0xffff;
			if (v < 12288 && !instruction_block(pps, v & ~3, block, false)) {
				writel(lower_32_bits(block[v & 3]), pps + 0xcc);
				writel(upper_32_bits(block[v & 3]), pps + 0xd0);
				writel(selected, pps + 0xc0);
			}
		}
		usleep_range(1000, 2000);
	}
	stopped_after = readl(pps + 4) & 0xff;
	ret = ret ?: restored;
	if (stopped_after)
		ret = ret ?: -EBUSY;
gate_out:
	writel(gate, glb + 0x1c);
unmap:
	iounmap(glb);
	iounmap(pps);
	return ret;
}

static int trace_show(struct seq_file *s, void *unused)
{
	unsigned int n, i;

	seq_printf(s, "result=%d restored=%d me=%d count=%u before=%x after=%x final_pc=%u\n",
		   result, restored, selected_me, count, stopped_before, stopped_after, final_pc);
	seq_printf(s, "entry_mask=%x\n", entry_mask);
	seq_printf(s, "queue=%d replayed=%u\n", queue, replayed);
	seq_printf(s, "hash_test=%u inserted=%u raw=%08x hash=%08x queue=%u ops=%u\n",
		   hash_test, hash_inserted, hash_raw, hash_value, hash_queue, hash_op_count);
	seq_printf(s, "late_hash=%u hash_pc=%u\n", late_hash, hash_replay_pc);
	seq_printf(s, "branch_test=%u result=%d count=%u\n",
		   branch_test, branch_result, branch_count);
	seq_printf(s, "branch_swap=%u\n", branch_swap);
	seq_printf(s, "hash_export=%u exported=%u value=%08x\n",
		   hash_export, hash_exported, exported_hash);
	seq_printf(s, "output_hash=%u\n", output_hash);
	seq_printf(s, "native_export=%u\n", native_export);
	seq_printf(s, "metadata_loads=%u word12=%08x word13=%08x\n",
		   metadata_loads, metadata_loaded[0], metadata_loaded[1]);
	seq_printf(s, "rss_map_test=%u result=%d count=%u\n",
		   rss_map_test, rss_map_result, rss_map_count);
	for (n = 0; n < rss_map_count; n++)
		seq_printf(s, "rss_map_case=%u result=%08x\n", n, rss_map_results[n]);
	for (n = 0; n < branch_count; n++) {
		unsigned int op = n / ARRAY_SIZE(branch_inputs);
		unsigned int input = n % ARRAY_SIZE(branch_inputs);

		seq_printf(s, "branch_cond=%04x spr10=%08x taken=%u\n",
			   branch_conditions[op], branch_inputs[input], branch_results[op][input]);
	}
	for (n = 0; n < hash_op_count; n++)
		seq_printf(s, "hash_op=%u inst=%016llx\n", n, hash_ops[n]);
	seq_printf(s, "calibrated=%u calibration_result=%d alu_count=%u flags=%08x/%08x\n",
		   calibrated, calibration_result, alu_count, alu_flags_before, alu_flags_after);
	for (n = 0; n < alu_count; n++) {
		unsigned int op = n / ARRAY_SIZE(alu_inputs), input = n % ARRAY_SIZE(alu_inputs);
		u64 inst = (alu_templates[op] & ~GENMASK_ULL(50, 19)) |
			   (3ULL << 46) | (3ULL << 35) | (8ULL << 24);

		seq_printf(s, "alu=%u inst=%016llx a=%08x b=%08x result=%08x\n",
			   op, inst, alu_inputs[input][0], alu_inputs[input][1],
			   alu_results[op][input]);
	}
	for (n = 0; n < 8; n++) {
		seq_printf(s, "entry=%u pc=%u", n, entry_pc[n]);
		if (entry_mask & BIT(n))
			for (i = 0; i < 64; i++)
				seq_printf(s, " w%u=%08x", i, entry_words[n][i]);
		seq_putc(s, '\n');
	}
	for (n = 0; n < count; n++) {
		seq_printf(s, "step=%u pc=%u inst=%016llx", n, samples[n].pc,
			   samples[n].instruction);
		for (i = 0; i < ARRAY_SIZE(samples[n].words); i++)
			if (!n || samples[n].words[i] != samples[n - 1].words[i])
				seq_printf(s, " w%u=%08x", i, samples[n].words[i]);
		seq_putc(s, '\n');
	}
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(trace);

static int __init rx_trace_init(void)
{
	const struct firmware *fw;
	u8 expected[128];
	size_t len;
	int ret;

	if (!frame || pc >= 5272 || steps < 1 || steps > 1024 || queue < -1 || queue > 15)
		return -EINVAL;
	len = strlen(frame) / 2;
	if (len < 64 || len > sizeof(expected) || strlen(frame) != len * 2 || len % 4)
		return -EINVAL;
	if (hex2bin(expected, frame, len))
		return -EINVAL;
	if (hash_test) {
		unsigned int network = 14;

		if (pc != 561 || alu_test || queue >= 0)
			return -EINVAL;
		if (get_unaligned_be16(expected + 12) == 0x8100) {
			network += 4;
			hash_tag_words = 1;
		}
		if (get_unaligned_be16(expected + network - 2) != 0x0800 ||
		    expected[network] != 0x45 ||
		    (get_unaligned_be16(expected + network + 6) & 0x3fff) ||
		    (expected[network + 9] != 6 && expected[network + 9] != 17))
			return -EINVAL;
	}
	ret = request_firmware(&fw, "zte/zx279133/mcode_intel.bin", NULL);
	if (ret)
		return ret;
	if (fw->size != 103456 || get_unaligned_be32(fw->data + 8) != 12288) {
		ret = -EINVAL;
		goto release;
	}
	samples = kvcalloc(steps, sizeof(*samples), GFP_KERNEL);
	if (!samples) {
		ret = -ENOMEM;
		goto release;
	}
	result = run_trace(fw, expected, len);
	directory = debugfs_create_dir("rx-ppu-trace", NULL);
	debugfs_create_file("trace", 0400, directory, NULL, &trace_fops);
	pr_info("rx-ppu-trace: result=%d restored=%d count=%u stopped=%x\n",
		result, restored, count, stopped_after);
release:
	release_firmware(fw);
	return ret;
}

static void __exit rx_trace_exit(void)
{
	debugfs_remove_recursive(directory);
	kvfree(samples);
}
module_init(rx_trace_init);
module_exit(rx_trace_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Bounded ordinary RX PPU trace and selected-frame queue probe");
