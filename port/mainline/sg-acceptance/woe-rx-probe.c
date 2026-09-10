// SPDX-License-Identifier: GPL-2.0
/*
 * RAM-only WOE descriptor parser experiment, not a network SG implementation.
 * This stops SIPC RX. Reboot the RAM FIT after collecting the output.
 * There is deliberately no module exit: DMA storage stays pinned until reboot.
 */
#include <linux/dma-mapping.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/platform_device.h>

#define WOE_BASE		0x19204000
#define SIPC_CFG		0x19004000
#define RING_SIZE	8
#define ARENA_SIZE	(16 * PAGE_SIZE)
#define PACKET_SIZE	96

static bool arm;
module_param(arm, bool, 0400);
MODULE_PARM_DESC(arm, "Stop SIPC RX for the experiment; requires a RAM reboot");

struct woe_desc {
	__le32 address;
	__le32 control;
	__le32 address1;
	__le32 info;
};

static void woe_snapshot(void __iomem *woe, const char *phase)
{
	pr_info("woe-sg: %s rd=%u wr=%u enter=%u eop=%u slices=%u sipc_sop=%u sipc_eop=%u diag=%#x/%#x\n",
		phase, readl(woe + 0x11ec), readl(woe + 0x11e8),
		readl(woe + 0x1914), readl(woe + 0x1918),
		readl(woe + 0x1948), readl(woe + 0x19c0) & 0xffff,
		readl(woe + 0x19d8) & 0xffff,
		readl(woe + 0x1808), readl(woe + 0x180c));
}

static int __init woe_probe_init(void)
{
	struct device *dev;
	struct woe_desc *ring;
	void __iomem *woe, *sipc;
	dma_addr_t dma;
	void *arena;
	u32 cfg;
	int i, ret = -ENOMEM;

	if (!arm)
		return -EINVAL;
	dev = bus_find_device_by_name(&platform_bus_type, NULL,
				      "19000000.ethernet");
	if (!dev)
		return -ENODEV;
	woe = ioremap(WOE_BASE, 0x2000);
	if (!woe)
		goto put_dev;
	sipc = ioremap(SIPC_CFG, sizeof(u32));
	if (!sipc)
		goto unmap_woe;

	/* Only use the inactive, recovered MT7915 descriptor configuration. */
	if (readl(woe) || readl(woe + 0x1000) ||
	    readl(woe + 0x11ec) || readl(woe + 0x11e8) ||
	    readl(woe + 0x1008) != 0x4041 ||
	    readl(woe + 0x100c) != 0x2010 ||
	    readl(woe + 0x1010) != 3 || readl(woe + 0x1030) ||
	    readl(woe + 0x1044) != 0x80001e01 ||
	    readl(woe + 0x1048) != 0x80000000) {
		pr_err("woe-sg: active engine or unexpected descriptor configuration\n");
		ret = -EBUSY;
		goto unmap_sipc;
	}
	arena = dma_alloc_coherent(dev, ARENA_SIZE, &dma, GFP_KERNEL);
	if (!arena)
		goto unmap_sipc;
	if (upper_32_bits(dma + ARENA_SIZE - 1)) {
		ret = -ERANGE;
		goto free_arena;
	}
	memset(arena, 0, ARENA_SIZE);
	ring = arena;
	for (i = 0; i < RING_SIZE; i++) {
		u32 control = (PACKET_SIZE << 16) | BIT(31);
		dma_addr_t buffer = dma + (i + 1) * PAGE_SIZE;
		u8 *data = arena + (i + 1) * PAGE_SIZE;

		/* Slot 0 is one packet; slots 1 and 2 have only the final LS set. */
		if (i != 1)
			control |= BIT(30);
		ring[i].address = cpu_to_le32(lower_32_bits(buffer));
		ring[i].control = cpu_to_le32(control);
		ring[i].address1 = cpu_to_le32(lower_32_bits(buffer + 128));
		memset(data + 32, 0xa0 + i, PACKET_SIZE - 32);
	}
	dma_wmb();
	woe_snapshot(woe, "before");
	pr_info("woe-sg: ring=%pad buffers=%#x/%#x/%#x; DMAD=16 RXD=32\n",
		&dma, le32_to_cpu(ring[0].address),
		le32_to_cpu(ring[1].address), le32_to_cpu(ring[2].address));

	/* Keep synthetic RX metadata out of PPU/IDM payload processing. */
	cfg = readl(sipc);
	writel(cfg & ~BIT(0), sipc);
	if (readl(sipc) & BIT(0)) {
		writel(cfg, sipc);
		ret = -EIO;
		goto free_arena;
	}
	usleep_range(1000, 2000);
	/* No PCIe producer polling, peer pointer writes, or interrupts. */
	writel(readl(woe + 0x1040) & ~BIT(31), woe + 0x1040);
	writel(0, woe + 0x104c);
	writel(lower_32_bits(dma), woe + 0x12a8);
	writel(RING_SIZE, woe + 0x1194);
	writel(BIT(0), woe + 0x1000);
	writel(BIT(1), woe);
	/* woe_enable(): common enable follows the ring and RX enables. */
	writel(BIT(1) | BIT(2), woe);
	writel(1, woe + 0x11e8);
	msleep(20);
	woe_snapshot(woe, "single");
	writel(3, woe + 0x11e8);
	msleep(20);
	woe_snapshot(woe, "split");
	writel(0, woe + 0x1000);
	writel(0, woe);
	readl(woe);
	msleep(20);
	woe_snapshot(woe, "stopped");
	pr_info("woe-sg: DMA arena retained; SIPC RX remains OFF; reboot RAM FIT now\n");
	/* Do not release mappings or device until a full reboot stops all DMA. */
	return 0;

free_arena:
	dma_free_coherent(dev, ARENA_SIZE, arena, dma);
unmap_sipc:
	iounmap(sipc);
unmap_woe:
	iounmap(woe);
put_dev:
	put_device(dev);
	return ret;
}
module_init(woe_probe_init);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("ZX279133 RAM-only WOE descriptor parser probe");
