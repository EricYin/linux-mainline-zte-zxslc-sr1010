// SPDX-License-Identifier: GPL-2.0-only
/* One retained CPU-IDM template descriptor. RAM reboot is mandatory. */
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/etherdevice.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/rtnetlink.h>
#include <linux/unaligned.h>
#include "zx279133.h"
#include "tso-ppu-trace.h"

static bool arm;
static bool template = true;
static bool pattern;
static uint version = 4;
static uint payload = 64;
static uint trace_steps;
static uint trace_pc = 1924;
module_param(arm, bool, 0400);
module_param(template, bool, 0400);
module_param(pattern, bool, 0400);
module_param(version, uint, 0400);
module_param(payload, uint, 0400);
module_param(trace_steps, uint, 0400);
module_param(trace_pc, uint, 0400);
MODULE_PARM_DESC(arm, "Submit one descriptor and keep TX stopped until RAM reboot");
MODULE_PARM_DESC(template, "Use vendor IDM flag3; false selects ordinary flag2 control");
MODULE_PARM_DESC(version, "IPv4 or basic IPv6 TCP template");
MODULE_PARM_DESC(payload, "Logical payload bytes: 64, 128 or 145; MSS is 64");
MODULE_PARM_DESC(pattern, "Two distinct mapped payload blocks for the 128-byte template test");
MODULE_PARM_DESC(trace_steps, "Trace up to 512 actual PPU instructions from CPU entry");

static const u8 peer_mac[ETH_ALEN] = { 0x00, 0xe0, 0x41, 0x68, 0x0d, 0x86 };

static u32 sum_bytes(const u8 *p, unsigned int len)
{
	u32 sum = 0;

	while (len >= 2) {
		sum += get_unaligned_be16(p);
		p += 2;
		len -= 2;
	}
	if (len)
		sum += *p << 8;
	return sum;
}

static u16 fold_sum(u32 sum)
{
	while (sum >> 16)
		sum = (sum & 0xffff) + (sum >> 16);
	return ~sum;
}

static unsigned int make_frame(u8 *frame, const u8 *source)
{
	unsigned int ip_len = version == 4 ? 20 : 40;
	u8 *ip = frame + ETH_HLEN, *tcp = ip + ip_len;
	u32 pseudo;
	unsigned int i;

	ether_addr_copy(frame, peer_mac);
	ether_addr_copy(frame + ETH_ALEN, source);
	put_unaligned_be16(version == 4 ? ETH_P_IP : ETH_P_IPV6, frame + 12);
	if (version == 4) {
		ip[0] = 0x45;
		put_unaligned_be16(104, ip + 2);
		put_unaligned_be16(0x5401, ip + 4);
		ip[8] = 64;
		ip[9] = IPPROTO_TCP;
		put_unaligned_be32(0xc0a80101, ip + 12);
		put_unaligned_be32(0xc0a80164, ip + 16);
		put_unaligned_be16(fold_sum(sum_bytes(ip, 20)), ip + 10);
		pseudo = sum_bytes(ip + 12, 8) + IPPROTO_TCP + 84;
	} else {
		ip[0] = 0x60;
		put_unaligned_be16(84, ip + 4);
		ip[6] = IPPROTO_TCP;
		ip[7] = 64;
		put_unaligned_be32(0x20010db8, ip + 8);
		put_unaligned_be16(1, ip + 12);
		ip[23] = 1;
		memcpy(ip + 24, ip + 8, 16);
		ip[39] = 0x64;
		pseudo = sum_bytes(ip + 8, 32) + IPPROTO_TCP + 84;
	}
	put_unaligned_be16(49041, tcp);
	put_unaligned_be16(49042, tcp + 2);
	put_unaligned_be32(0x10203040, tcp + 4);
	put_unaligned_be32(0x50607080, tcp + 8);
	tcp[12] = 0x50;
	tcp[13] = 0x18;
	put_unaligned_be16(4096, tcp + 14);
	if (pattern)
		for (i = 0; i < 128; i++)
			tcp[20 + i] = (i < 64 ? 0x31 : 0x71) + (i & 15);
	put_unaligned_be16(fold_sum(pseudo + sum_bytes(tcp, 84)), tcp + 16);
	return ETH_HLEN + ip_len + 84;
}

static int __init tso_template_init(void)
{
	struct net_device *ndev;
	struct zx279133_eth *eth;
	struct zx279133_tx_ring *tx;
	struct zx279133_idm_desc *desc;
	void *arena;
	dma_addr_t dma;
	u64 start, first_done = 0;
	u32 done_reg, doorbell, length, mapping_length, count, tail;
	u16 before, after, producer;
	unsigned int i;
	struct sk_buff *control, *source;
	int ret = 0;

	if (!arm || (version != 4 && version != 6) ||
	    (payload != 64 && payload != 128 && payload != 145) ||
	    (!template && payload != 64) || (pattern && (!template || payload != 128)) ||
	    trace_steps > 512 || (trace_pc != 1924 && trace_pc != 2053 &&
				  trace_pc != 2054 && trace_pc != 2058))
		return -EINVAL;
	ndev = dev_get_by_name(&init_net, "eth0");
	if (!ndev)
		return -ENODEV;
	eth = netdev_priv(ndev);
	if (strcmp(dev_driver_string(eth->dev), "zx279133-eth")) {
		ret = -ENODEV;
		goto put_net;
	}
	source = netdev_alloc_skb(ndev, PAGE_SIZE);
	if (!source) {
		ret = -ENOMEM;
		goto put_net;
	}
	arena = skb_put(source, PAGE_SIZE);
	memset(arena, 0, PAGE_SIZE);
	length = make_frame(arena, ndev->dev_addr);
	mapping_length = length + (pattern ? 64 : 0);
	/* Same complete frame through the validated driver before isolated submission. */
	control = netdev_alloc_skb(ndev, length);
	if (!control) {
		ret = -ENOMEM;
		goto free_source;
	}
	skb_put_data(control, arena, length);
	skb_reset_mac_header(control);
	skb_set_network_header(control, ETH_HLEN);
	skb_set_transport_header(control, ETH_HLEN + (version == 4 ? 20 : 40));
	control->protocol = htons(version == 4 ? ETH_P_IP : ETH_P_IPV6);
	pr_info("tso-template: driver_control_begin=1\n");
	ret = dev_queue_xmit(control);
	if (ret)
		goto free_source;
	msleep(100);
	spin_lock_bh(&eth->tx_lock);
	for (i = 0; i < ZX279133_CPU_TX_QUEUES; i++) {
		unsigned int last = (eth->tx[i].producer - 1) & (ZX279133_IDM_TX_DEPTH - 1);
		struct zx279133_idm_desc *previous = eth->tx_descs +
			(ZX279133_IDM_CPU_TX_FIRST + i) * ZX279133_IDM_TX_DEPTH + last;

		pr_info("tso-template: driver_queue=%u last=%u words=%08x/%08x/%08x/%08x\n",
			i, last, le32_to_cpu(previous->address),
			le32_to_cpu(previous->length_flags), le32_to_cpu(previous->metadata[0]),
			le32_to_cpu(previous->metadata[4]));
	}
	spin_unlock_bh(&eth->tx_lock);
	/* Match the validated TX allocation/headroom and streaming DMA mapping. */
	dma = dma_map_single(eth->dev, arena, mapping_length, DMA_TO_DEVICE);
	if (dma_mapping_error(eth->dev, dma)) {
		ret = -EIO;
		goto free_source;
	}
	if (upper_32_bits(dma)) {
		ret = -ERANGE;
		goto free_arena;
	}
	count = payload > 64 ? (payload - 1) / 64 : 0;
	tail = payload - count * 64;
	tx = &eth->tx[0];
	done_reg = zx279133_idm_tx_done_reg(ZX279133_IDM_CPU_TX_FIRST);
	doorbell = zx279133_idm_tx_doorbell_reg(ZX279133_IDM_CPU_TX_FIRST);
	rtnl_lock();
	mutex_lock(&eth->datapath_lock);
	if (!netif_running(ndev) || !eth->tx_prepared || eth->tx_stopping || eth->xsk_pool) {
		ret = -EBUSY;
		goto unlock;
	}
	WRITE_ONCE(eth->tx_stopping, true);
	netif_tx_disable(ndev);
	if (eth->lan_ndev)
		netif_tx_disable(eth->lan_ndev);
	cancel_delayed_work_sync(&eth->tx_reclaim_work);
	spin_lock_bh(&eth->tx_lock);
	if (eth->tx[0].pending || eth->tx[1].pending ||
	    eth->tx[0].notify_pending || eth->tx[1].notify_pending) {
		spin_unlock_bh(&eth->tx_lock);
		/* No diagnostic descriptor was published; return ordinary TX. */
		WRITE_ONCE(eth->tx_stopping, false);
		netif_tx_wake_all_queues(ndev);
		if (eth->lan_ndev)
			netif_tx_wake_all_queues(eth->lan_ndev);
		mod_delayed_work(system_wq, &eth->tx_reclaim_work, 1);
		ret = -EBUSY;
		goto unlock;
	}
	producer = tx->producer;
	/* This isolated fixture stays offline; do not arm ordinary TX watchdog recovery. */
	netif_device_detach(ndev);
	if (eth->lan_ndev)
		netif_device_detach(eth->lan_ndev);
	spin_unlock_bh(&eth->tx_lock);
	if (trace_steps) {
		ret = tso_trace_prepare(trace_steps, trace_pc);
		if (ret) {
			tso_trace_finish();
			pr_info("tso-trace: prepare_failed=%d retained=1 reboot_required=1\n", ret);
			goto retained;
		}
	}
	spin_lock_bh(&eth->tx_lock);
	desc = eth->tx_descs + ZX279133_IDM_CPU_TX_FIRST * ZX279133_IDM_TX_DEPTH + producer;
	memset(desc, 0, sizeof(*desc));
	desc->address = cpu_to_le32(lower_32_bits(dma));
	desc->length_flags = cpu_to_le32(0x0f000000 | length << 1);
	desc->metadata[0] = cpu_to_le32(0x00460000);
	desc->metadata[4] = cpu_to_le32(template ? 0x0c000000 : 0x08000000);
	if (template && count) {
		desc->metadata[2] = cpu_to_le32(count << 16);
		desc->metadata[3] = cpu_to_le32(64 << 16 | tail);
	}
	/* Empty software owner: no automatic unmap/free of retained source. */
	memset(&tx->slots[producer], 0, sizeof(tx->slots[producer]));
	tx->producer = (producer + 1) & (ZX279133_IDM_TX_DEPTH - 1);
	tx->pending++;
	before = readl(eth->base + ZX279133_IDM_BASE + done_reg);
	pr_info("tso-template: version=%u template=%u payload=%u mss=64 length=%u dma=%pad producer=%u done_before=%u\n",
		version, template, payload, length, &dma, producer, before);
	pr_info("tso-template: pattern=%u mapped_length=%u retained_skb=1\n",
		pattern, mapping_length);
	for (i = 0; i < 8; i++)
		pr_info("tso-template: desc[%u]=%08x\n", i, le32_to_cpu(((__le32 *)desc)[i]));
	start = ktime_get_ns();
	/* Publish the mapped frame and initialized descriptor before the doorbell. */
	wmb();
	writel(1 << ZX279133_IDM_TX_DOORBELL_COUNT_SHIFT,
	       eth->base + ZX279133_IDM_BASE + doorbell);
	tx->submitted++;
	eth->tx_doorbell_writes++;
	eth->tx_doorbell_descs++;
	spin_unlock_bh(&eth->tx_lock);
	if (trace_steps) {
		ret = tso_trace_capture(eth, arena, length, trace_steps, done_reg);
		tso_trace_finish();
		pr_info("tso-trace: capture_result=%d\n", ret);
	}
	for (i = 0; i < 1000; i++) {
		after = readl(eth->base + ZX279133_IDM_BASE + done_reg);
		if (after != before && !first_done)
			first_done = ktime_get_ns() - start;
		usleep_range(1000, 1200);
	}
	pr_info("tso-template: done_after=%u delta=%u first_change_ns=%llu retained=1 tx_stopped=1 reboot_required=1\n",
		after, (u16)(after - before), first_done);
retained:
	mutex_unlock(&eth->datapath_lock);
	rtnl_unlock();
	/* No module exit: source mapping, skb and device reference survive until reboot. */
	return 0;
unlock:
	mutex_unlock(&eth->datapath_lock);
	rtnl_unlock();
free_arena:
	dma_unmap_single(eth->dev, dma, mapping_length, DMA_TO_DEVICE);
free_source:
	dev_kfree_skb(source);
put_net:
	dev_put(ndev);
	return ret;
}
module_init(tso_template_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Retained RAM-only IDM template segmentation diagnostic");
