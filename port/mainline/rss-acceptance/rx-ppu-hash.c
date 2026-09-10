// SPDX-License-Identifier: GPL-2.0-only
/* Temporary continuous PPU hash diagnostic. Never part of the production FIT. */
#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/seq_file.h>
#include <linux/unaligned.h>

#define HOOKS 2
#define WORDS 256

static void __iomem *pps, *glb;
static struct dentry *directory;
static const u32 sites[HOOKS] = { 1558, 1893 };
static const u32 caves[HOOKS] = { 6000, 6288 };
static u64 saved_hooks[HOOKS][4], saved_code[HOOKS][WORDS];
static u64 code[HOOKS][WORDS];
static u32 lengths[HOOKS], saved_count, hooked;
static int restore_result;
static bool audit_only;
module_param(audit_only, bool, 0400);
static bool map_test;
module_param(map_test, bool, 0400);
static unsigned int wan_map = 0xf0;
module_param(wan_map, uint, 0400);
static unsigned int lan_map = 0xf0;
module_param(lan_map, uint, 0400);

static u64 copy_bits(unsigned int dst, unsigned int offset,
		     unsigned int src, unsigned int bit, unsigned int width)
{
	return 0x0500000000000520ULL | (u64)dst << 46 | (u64)offset << 41 |
	       (u64)src << 35 | (u64)bit << 30 | (width - 1);
}

static const u64 common_ops[] = {
	0x0700800028180523ULL,
	0x0704000028200523ULL,
	0x06208010100003ffULL,
	0x0704000008000523ULL,
	0x06208010100003ffULL,
	0x0704000028120520ULL,
	0x0504008000008527ULL,
	0x06208010100003ffULL,
	0x050400140000852fULL,
	0x06208010100003ffULL,
	0x0504001200008537ULL,
	0x06208010100003ffULL,
	0x050400110000853bULL,
	0x06208010100003ffULL,
	0x05040010c000853cULL,
	0x06208010100003ffULL,
	0x010400000000852fULL,
};

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

static u64 branch(u32 from, u32 to)
{
	return 0x0108000000000520ULL | (u64)(from + 1) << 31 | (u64)to << 15;
}

static u64 branch_if(u32 condition, u32 yes, u32 no)
{
	return 0x0108000000000000ULL | (u64)no << 31 | (u64)yes << 15 | condition;
}

static void emit(u64 *p, unsigned int *n, u64 inst)
{
	p[(*n)++] = inst;
	/* Retain a producer/use gap, as in the native queue-read/NOP/add sequence. */
	p[(*n)++] = 0;
}

static unsigned int emit_map(unsigned int which, unsigned int *n,
			     unsigned int depth, unsigned int bucket,
			     unsigned int map)
{
	u64 *p = code[which];
	unsigned int start = caves[which] + *n;

	if (depth == 3) {
		unsigned int queue = (bucket & 3) | (((map >> bucket) & 1) << 2);

		emit(p, n, copy_bits(10, 0, 16, 0, 32));
		emit(p, n, 0x010400000000852fULL);
		emit(p, n, 0x0504210680000523ULL);
		emit(p, n, 0x0104200000000522ULL | (u64)queue << 25);
		p[*n] = branch(caves[which] + *n, sites[which] + 1);
		(*n)++;
	} else {
		unsigned int at = (*n)++;
		unsigned int no = emit_map(which, n, depth + 1, bucket, map);
		unsigned int yes = emit_map(which, n, depth + 1, bucket | BIT(depth), map);

		p[at] = branch_if(0x2880 + depth * 0x20, yes, no);
	}
	return start;
}

static void make_program(unsigned int which)
{
	u64 *p = code[which];
	unsigned int n = 4, i, fallback;

	if (which) {
		emit(p, &n, 0x0601a092148000e7ULL);
		emit(p, &n, 0x06018090148000e7ULL);
	}
	for (i = 0; i < ARRAY_SIZE(common_ops) - 1; i++)
		emit(p, &n, common_ops[i]);
	if (which)
		emit(p, &n, 0x050180a400008527ULL);
	if (map_test) {
		unsigned int at, lan, wan;

		/* SPR16 is scratch here; restore SPR10 before the native queue read. */
		emit(p, &n, copy_bits(16, 0, 10, 0, 32));
		emit(p, &n, copy_bits(10, 4, 2, 0, 3));
		emit(p, &n, copy_bits(10, 3, 32, 29, 1));
		at = n++;
		lan = emit_map(which, &n, 0, 0, lan_map);
		wan = emit_map(which, &n, 0, 0, wan_map);
		p[at] = branch_if(0x2860, wan, lan);
	} else {
		emit(p, &n, common_ops[ARRAY_SIZE(common_ops) - 1]);
		emit(p, &n, 0x0504210680000523ULL);
		emit(p, &n, 0x0504201000000522ULL);
		p[n] = branch(caves[which] + n, sites[which] + 1);
		n++;
	}
	fallback = caves[which] + n;
	/* Rejected formats execute exactly the displaced instruction and return. */
	emit(p, &n, 0x0504210680000523ULL);
	p[n] = branch(caves[which] + n, sites[which] + 1);
	lengths[which] = ALIGN(n + 1, 4);
	/* Calibrated SPR10 predicates: IPv4 bit4, fragment bit21, UDP bit5, TCP bit6. */
	p[0] = branch_if(0x2880, caves[which] + 1, fallback);
	p[1] = branch_if(0x2aa0, fallback, caves[which] + 2);
	p[2] = branch_if(0x28a0, caves[which] + 4, caves[which] + 3);
	p[3] = branch_if(0x28c0, caves[which] + 4, fallback);
}

static bool cave_busy(void)
{
	u32 busy = readl(pps + 0x18), pc;
	int me, which;

	for (me = 0; me < 8; me++) {
		if (!(busy & BIT(me)))
			continue;
		pc = readl(pps + 0x100 + me * 4) & 0xffff;
		for (which = 0; which < HOOKS; which++)
			if (pc >= caves[which] && pc < caves[which] + lengths[which])
				return true;
	}
	return false;
}

static int restore_patch(void)
{
	u32 gate = readl(glb + 0x1c);
	int which, i, ret = 0, error;

	writel(gate & ~BIT(3), glb + 0x1c);
	for (which = 0; which < hooked; which++) {
		error = instruction_block(pps, sites[which] & ~3, saved_hooks[which], true);
		ret = ret ?: error;
	}
	if (ret)
		goto out;
	hooked = 0;
	/* Hooks are gone; let contexts which already fetched them leave the bodies. */
	msleep(20);
	if (cave_busy() || (readl(pps + 4) & 0xff)) {
		ret = -EBUSY;
		goto out;
	}
	for (which = 0; which < saved_count; which++)
		for (i = 0; i < lengths[which]; i += 4) {
			error = instruction_block(pps, caves[which] + i,
						  saved_code[which] + i, true);
			ret = ret ?: error;
		}
out:
	writel(gate, glb + 0x1c);
	return ret;
}

static int patch_show(struct seq_file *s, void *unused)
{
	unsigned int which, i;
	u64 block[4];
	u32 gate = readl(glb + 0x1c), mismatches = 0;
	int ret = 0;

	writel(gate & ~BIT(3), glb + 0x1c);
	for (which = 0; which < HOOKS && !ret; which++) {
		ret = instruction_block(pps, sites[which] & ~3, block, false);
		if (ret)
			break;
		if (block[sites[which] & 3] != branch(sites[which], caves[which]))
			mismatches++;
		for (i = 0; i < lengths[which]; i += 4) {
			ret = instruction_block(pps, caves[which] + i, block, false);
			if (ret)
				break;
			if (memcmp(block, code[which] + i, sizeof(block)))
				mismatches++;
		}
	}
	writel(gate, glb + 0x1c);

	seq_printf(s, "hooked=%u restore_result=%d stopped=%08x\n",
		   hooked, restore_result, readl(pps + 4) & 0xff);
	seq_printf(s, "live_read=%d mismatched_blocks=%u\n", ret, mismatches);
	seq_printf(s, "map_test=%u wan_map=%02x lan_map=%02x\n",
		   map_test, wan_map, lan_map);
	for (which = 0; which < HOOKS; which++) {
		seq_printf(s, "site=%u cave=%u words=%u\n",
			   sites[which], caves[which], lengths[which]);
		for (i = 0; i < lengths[which]; i++)
			seq_printf(s, "pc=%u inst=%016llx\n", caves[which] + i, code[which][i]);
	}
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(patch);

static int __init rx_hash_init(void)
{
	const struct firmware *fw;
	u64 block[4];
	u32 gate;
	int which, i, j, ret;

	if (wan_map > 0xff || lan_map > 0xff)
		return -EINVAL;

	ret = request_firmware(&fw, "zte/zx279133/mcode_intel.bin", NULL);
	if (ret)
		return ret;
	if (fw->size != 103456 || get_unaligned_be32(fw->data + 12) != 5272) {
		ret = -EINVAL;
		goto release;
	}
	pps = ioremap(0x18090000, 0xa000);
	glb = ioremap(0x18000000, 0x100);
	if (!pps || !glb) {
		ret = -ENOMEM;
		goto unmap;
	}
	if (readl(pps + 4) & 0xff) {
		ret = -EBUSY;
		goto unmap;
	}
	gate = readl(glb + 0x1c);
	writel(gate & ~BIT(3), glb + 0x1c);
	/* Mainline loads these two ranges; the gap retains unspecified SRAM data. */
	for (i = 0; i < 12288; i += 4) {
		if (i == 5272)
			i = 12208;
		ret = instruction_block(pps, i, block, false);
		if (ret)
			goto gate_out;
		for (j = 0; j < 4; j++)
			if (block[j] != get_unaligned_be64(fw->data + 16 + (i + j) * 8)) {
				pr_info("rx-ppu-hash: loaded image mismatch pc=%u\n", i + j);
				ret = -EINVAL;
				goto gate_out;
			}
	}
	pr_info("rx-ppu-hash: all 5352 loaded instructions match firmware\n");
	if (audit_only) {
		u32 run = 0, best = 0, end = 0, empty = 0;

		for (i = 5272; i < 12208; i += 4) {
			ret = instruction_block(pps, i, block, false);
			if (ret)
				goto gate_out;
			for (j = 0; j < 4; j++) {
				if (!block[j] || block[j] == 0x0018000000000040ULL) {
					run++;
					empty++;
				} else {
					run = 0;
				}
				if (run > best) {
					best = run;
					end = i + j + 1;
				}
			}
		}
		pr_info("rx-ppu-hash: audit empty=%u longest=[%u,%u) words=%u\n",
			empty, end - best, end, best);
		ret = -EAGAIN;
		goto gate_out;
	}
	for (which = 0; which < HOOKS; which++) {
		make_program(which);
		ret = instruction_block(pps, sites[which] & ~3, saved_hooks[which], false);
		if (ret)
			goto gate_out;
		for (i = 0; i < 4; i++)
			if (saved_hooks[which][i] != get_unaligned_be64(fw->data + 16 +
						((sites[which] & ~3) + i) * 8)) {
				ret = -EINVAL;
				goto gate_out;
			}
		for (i = 0; i < lengths[which]; i += 4) {
			ret = instruction_block(pps, caves[which] + i,
						saved_code[which] + i, false);
			if (ret)
				goto gate_out;
			for (j = 0; j < 4; j++)
				if (get_unaligned_be64(fw->data + 16 +
					(caves[which] + i + j) * 8) != 0x0018000000000040ULL) {
					ret = -EINVAL;
					goto gate_out;
				}
		}
		saved_count++;
	}
	for (which = 0; which < HOOKS; which++)
		for (i = 0; i < lengths[which]; i += 4) {
			ret = instruction_block(pps, caves[which] + i, code[which] + i, true);
			if (ret)
				goto rollback;
		}
	for (which = 0; which < HOOKS; which++) {
		memcpy(block, saved_hooks[which], sizeof(block));
		block[sites[which] & 3] = branch(sites[which], caves[which]);
		hooked = which + 1;
		ret = instruction_block(pps, sites[which] & ~3, block, true);
		if (ret)
			goto rollback;
	}
	writel(gate, glb + 0x1c);
	release_firmware(fw);
	directory = debugfs_create_dir("rx-ppu-hash", NULL);
	debugfs_create_file("status", 0400, directory, NULL, &patch_fops);
	pr_info("rx-ppu-hash: continuous diagnostic installed, hooks=2\n");
	return 0;
rollback:
	restore_result = restore_patch();
	pr_err("rx-ppu-hash: install=%d restore=%d; RAM recovery required on restore error\n",
	       ret, restore_result);
gate_out:
	writel(gate, glb + 0x1c);
unmap:
	iounmap(glb);
	iounmap(pps);
release:
	release_firmware(fw);
	return ret;
}

static void __exit rx_hash_exit(void)
{
	debugfs_remove_recursive(directory);
	restore_result = restore_patch();
	pr_info("rx-ppu-hash: hooks/body restore=%d\n", restore_result);
	iounmap(glb);
	iounmap(pps);
}

module_init(rx_hash_init);
module_exit(rx_hash_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Temporary IPv4 TCP/UDP RX tuple hash with native format fallback");
