/* SPDX-License-Identifier: GPL-2.0-only */
/* Calibrated CPU-entry breakpoint and unmodified instruction replay. */
#include <linux/debugfs.h>
#include <linux/iopoll.h>
#include <linux/slab.h>

struct tso_trace_record {
	u32 step;
	u32 pc;
	u32 flags;
	u32 done;
	u32 me;
	u32 reserved;
	u64 instruction;
	u32 packet[64];
	u32 spr[32];
	u32 response[32];
};

static_assert(sizeof(struct tso_trace_record) == 544);

struct tso_trace {
	void __iomem *pps;
	void __iomem *global;
	u32 gate;
	u64 original[4];
	struct tso_trace_record *records;
	struct debugfs_blob_wrapper blob;
	u32 count;
	u32 point;
	bool armed;
	bool gate_saved;
};

static struct tso_trace trace;

static int tso_inst_command(u32 command)
{
	u32 value;
	int ret;

	ret = readl_poll_timeout_atomic(trace.pps + 0xd04, value, value & 1, 1, 1000);
	if (ret)
		return ret;
	writel((readl(trace.pps + 0xd00) & ~0x1fff) | command, trace.pps + 0xd00);
	return readl_poll_timeout_atomic(trace.pps + 0xd00, value, value & 1, 1, 1000);
}

static int tso_inst_block(u32 pc, u64 *block, bool write)
{
	u32 value;
	int i, ret;

	if (pc >= 12288)
		return -EINVAL;
	if (write) {
		ret = readl_poll_timeout_atomic(trace.pps + 0xd04, value, value & 1, 1, 1000);
		if (ret)
			return ret;
		for (i = 0; i < 4; i++) {
			writel(lower_32_bits(block[i]), trace.pps + 0xd0c + i * 8);
			writel((readl(trace.pps + 0xd10 + i * 8) & ~0x7ffffff) |
			       upper_32_bits(block[i]), trace.pps + 0xd10 + i * 8);
		}
		ret = tso_inst_command(pc >> 2);
		if (ret)
			return ret;
	}
	ret = tso_inst_command(BIT(12) | (pc >> 2));
	if (ret)
		return ret;
	for (i = 0; i < 4; i++) {
		u64 actual = (u64)(readl(trace.pps + 0xd5c + i * 8) & 0x7ffffff) << 32 |
			     readl(trace.pps + 0xd58 + i * 8);

		if (write && actual != block[i])
			return -EIO;
		block[i] = actual;
	}
	return 0;
}

static void tso_replay(u32 mask, u64 instruction, bool step)
{
	writel(lower_32_bits(instruction), trace.pps + 0xcc);
	writel(upper_32_bits(instruction), trace.pps + 0xd0);
	writel(mask, trace.pps + (step ? 0xc4 : 0xc0));
}

static int tso_trace_prepare(unsigned int steps, u32 point)
{
	static const u64 expected[4] = {
		0x0700c00018280523ULL, 0x0701400018380527ULL,
		0x0702400018d80523ULL, 0x0707800018500523ULL,
	};
	static const u64 fork_block[4] = {
		0x0018000000000801ULL, 0x0290427800056405ULL,
		0x020500a0000101efULL, 0x0108040404358520ULL,
	};
	static const u64 repeat_block[4] = {
		0x0140000000000523ULL, 0x0018000021800002ULL,
		0x0702c00018b80523ULL, 0x0702400018d80523ULL,
	};
	const u64 *expected_block = point == 1924 ? expected :
				    point == 2058 ? repeat_block : fork_block;
	u64 block[4];
	int ret;

	trace.point = point;
	trace.records = kcalloc(steps, sizeof(*trace.records), GFP_KERNEL);
	trace.pps = ioremap(0x18090000, 0xa000);
	trace.global = ioremap(0x18000000, 0x100);
	if (!trace.records || !trace.pps || !trace.global)
		return -ENOMEM;
	if (readl(trace.pps + 4) & 0xff)
		return -EBUSY;
	trace.gate = readl(trace.global + 0x1c);
	trace.gate_saved = true;
	writel(trace.gate & ~BIT(3), trace.global + 0x1c);
	ret = tso_inst_block(point & ~3, trace.original, false);
	if (ret || memcmp(trace.original, expected_block, sizeof(expected)))
		return ret ?: -EINVAL;
	memcpy(block, trace.original, sizeof(block));
	block[point & 3] = 0x0080000017d80000ULL;
	writel(lower_32_bits(trace.original[point & 3]), trace.pps + 0xcc);
	writel(upper_32_bits(trace.original[point & 3]), trace.pps + 0xd0);
	trace.armed = true;
	return tso_inst_block(point & ~3, block, true);
}

static int tso_trace_capture(struct zx279133_eth *eth, const u8 *frame,
			     unsigned int length, unsigned int steps, u32 done_reg)
{
	struct dentry *directory;
	u64 block[4];
	u32 stopped, selected = 0, value, pc = trace.point;
	unsigned int i, me;
	int ret, restored, result = 0;

	msleep(20);
	stopped = readl(trace.pps + 4) & 0xff;
	for (me = 0; me < 8; me++) {
		void __iomem *packet = trace.pps + 0x4480 + me * 0x400;

		if (!(stopped & BIT(me)))
			continue;
		for (i = 0; i < min(length, 128U) / 4; i++)
			if (readl(packet + i * 4) != get_unaligned_be32(frame + i * 4))
				break;
		if (i == min(length, 128U) / 4)
			selected |= BIT(me);
	}
	restored = tso_inst_block(trace.point & ~3, trace.original, true);
	if (restored)
		return restored;
	trace.armed = false;
	if (stopped & ~selected)
		tso_replay(stopped & ~selected, trace.original[trace.point & 3], false);
	pr_info("tso-trace: stopped=%02x selected=%02x restored=%d\n", stopped, selected, restored);
	if (!selected)
		return -ENODATA;
	ret = 0;
	for (me = 0; me < 8; me++) {
		u32 active_me = BIT(me);

		if (!(selected & active_me))
			continue;
		while (trace.count < steps) {
			struct tso_trace_record *record = &trace.records[trace.count];
			void __iomem *packet = trace.pps + 0x4400 + me * 0x400;
			void __iomem *spr = trace.pps + 0x4500 + me * 0x400;

			if (!(readl(trace.pps + 4) & active_me))
				break;
			pc = readl(trace.pps + 0x100 + me * 4) & 0xffff;
			ret = tso_inst_block(pc & ~3, block, false);
			if (ret)
				break;
			record->step = trace.count;
			record->me = me;
			record->pc = pc;
			record->flags = readl(trace.pps + 0x4600 + me * 0x400);
			record->done = readl(eth->base + ZX279133_IDM_BASE + done_reg) & 0xffff;
			record->instruction = block[pc & 3];
			for (i = 0; i < 64; i++)
				record->packet[i] = readl(packet + i * 4);
			for (i = 0; i < 32; i++) {
				record->spr[i] = readl(spr + i * 4);
				record->response[i] = readl(spr + 0x80 + i * 4);
			}
			trace.count++;
			tso_replay(active_me, record->instruction, true);
			usleep_range(100, 200);
			ret = readl_poll_timeout(trace.pps + 4, value,
						 (value & active_me) ||
						 !(readl(trace.pps + 0x18) & active_me),
						 10, 10000);
			if (ret)
				break;
		}
		if (readl(trace.pps + 4) & active_me) {
			pc = readl(trace.pps + 0x100 + me * 4) & 0xffff;
			if (!tso_inst_block(pc & ~3, block, false))
				tso_replay(active_me, block[pc & 3], false);
		}
		if (ret)
			result = ret;
	}
	pr_info("tso-trace: steps=%u result=%d stopped=%02x busy=%02x last_pc=%u record_size=%zu\n",
		trace.count, result, readl(trace.pps + 4) & 0xff,
		readl(trace.pps + 0x18) & 0xff, pc, sizeof(*trace.records));
	trace.blob.data = trace.records;
	trace.blob.size = trace.count * sizeof(*trace.records);
	directory = debugfs_create_dir("tso-template", NULL);
	debugfs_create_blob("trace", 0400, directory, &trace.blob);
	return result;
}

static void tso_trace_finish(void)
{
	if (trace.armed)
		tso_inst_block(trace.point & ~3, trace.original, true);
	if (trace.gate_saved)
		writel(trace.gate, trace.global + 0x1c);
}
