// SPDX-License-Identifier: GPL-2.0-only
/* ZTE ZX279133 NPPT IPv4/IPv6 flow-table offload. */

#include <linux/bitmap.h>
#include <linux/debugfs.h>
#include <linux/etherdevice.h>
#include <linux/iopoll.h>
#include <linux/ip.h>
#include <linux/if_pppox.h>
#include <linux/ipv6.h>
#include <linux/jiffies.h>
#include <linux/rhashtable.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/tcp.h>
#include <linux/udp.h>
#include <linux/unaligned.h>
#include <linux/xarray.h>

#include <net/flow_offload.h>
#include <net/dsa.h>
#include <net/dsa_zx279133_rtl8372n.h>
#include <net/pkt_cls.h>

#include "zx279133.h"

#define ZX279133_FAST_KEY_SIZE		16
#define ZX279133_FAST_DATA_SIZE		32
#define ZX279133_FAST_IKEY_SIZE		16
#define ZX279133_FAST_IKEY_DEPTH	4096
#define ZX279133_ZCAM_BLOCKS		4
#define ZX279133_ZCAM_CELLS		5
#define ZX279133_ZCAM_ADDRS		256
#define ZX279133_ZCAM_ENTRY_SIZE	64
#define ZX279133_ZCAM_SLOTS		(ZX279133_ZCAM_BLOCKS * \
					 ZX279133_ZCAM_CELLS * \
					 ZX279133_ZCAM_ADDRS)
/* np.ko core_sdt_init() gives SDT14 256 ZCAM entries before DDR overflow. */
#define ZX279133_FAST_FULL_ZCAM_LIMIT	256
#define ZX279133_DDR_BUCKET_SHIFT	6
#define ZX279133_DDR_BUCKET_SIZE		BIT(ZX279133_DDR_BUCKET_SHIFT)
#define ZX279133_DDR_BUCKETS		(ZX279133_SE_HASH_REQUIRED_SIZE >> \
					 ZX279133_DDR_BUCKET_SHIFT)
#define ZX279133_HASH_CRC32_POLY		0x04c11db7

#define ZX279133_SE_ALG_WR_DONE		0x50000
#define ZX279133_SE_ALG_WR_CMD		0x50004
#define ZX279133_SE_ALG_WR_DATA		0x50008
#define ZX279133_SE_HASH_AGCLK		BIT(2)
#define ZX279133_FAST_MULTI_SELECTOR	0x01
#define ZX279133_FAST_MULTI_FOOTER	0xc1
#define ZX279133_FAST_FULL_SELECTOR	0x00
#define ZX279133_FAST_FULL_FOOTER	0xe0

/* SDT 9 host entries: CRC selector and the valid/type bits of word 3. */
#define ZX279133_HOST_SELECTOR		0x00
#define ZX279133_HOST_FLAGS		0xa0000000

enum zx279133_flow_port {
	ZX279133_FLOW_PORT_NONE,
	ZX279133_FLOW_PORT_LAN,
	ZX279133_FLOW_PORT_WAN,
};

struct zx279133_flow_tuple {
	__be32 src_addr;
	__be32 dst_addr;
	__be16 src_port;
	__be16 dst_port;
};

struct zx279133_flow_data {
	struct ethhdr eth;
	struct zx279133_flow_tuple tuple;
	struct in6_addr src_v6;
	struct in6_addr dst_v6;
};

/*
 * The LAN entry program (PC 10) first looks the source MAC of every frame up
 * in the SDT 9 MAC table, and takes only a hit on to the flow table; a miss
 * goes to the CPU. np.ko keeps that table in step with the bridge. Here a LAN
 * host gets an entry while it has an offloaded downstream flow: the flow's
 * destination MAC is the host, and its egress VID is the host's logical port.
 */
struct zx279133_flow_host {
	struct list_head node;
	u8 mac[ETH_ALEN];
	u16 port;
	u8 zblock;
	u8 zcell;
	u8 zaddr;
	unsigned int users;
};

struct zx279133_flow_entry {
	struct rhash_head key_node;
	u8 key[ZX279133_FAST_KEY_SIZE];
	/* An upstream entry is indexed by the key its reply will have. */
	struct rhash_head reply_node;
	u8 reply_key[ZX279133_FAST_KEY_SIZE];
	u8 zblock;
	u8 zcell;
	u8 zaddr;
	u32 ddr_bucket;
	u32 age;
	u16 ikey;
	u16 stat;
	u8 wanid;
	struct zx279133_flow_host *host;
	/* The flowtable the entry came from. */
	const struct flow_block *fblock;
	u64 packets;
	u64 bytes;
	unsigned long lastused;
	bool has_stat;
	bool has_age;
	bool snat;
	bool pppoe;
	/* From a LAN port, out by the WAN path; else from the WAN port. */
	bool upstream;
	/* Out of a LAN port, a second WAN, rather than the WAN port. */
	bool lan_routed;
	/*
	 * Kept out of the hardware, holding nothing there: only remembers,
	 * by its reply key, that the other half of the flow comes in PPPoE.
	 */
	bool refused;
	bool reply_indexed;
	bool full;
	bool in_ddr;
};

struct zx279133_flow_offload {
	struct zx279133_eth *eth;
	/* Serializes software flow state and SE indirect accesses. */
	struct mutex lock;
	struct xarray flows;
	struct rhashtable key_flows;
	struct rhashtable reply_flows;
	struct list_head block_cb_list;
	struct list_head hosts;
	DECLARE_BITMAP(zcam_used, ZX279133_ZCAM_SLOTS);
	unsigned long *ddr_used;
	DECLARE_BITMAP(ikey_used, ZX279133_FAST_IKEY_DEPTH);
	DECLARE_BITMAP(age_used, ZX279133_FAST_AGE_DEPTH);
	DECLARE_BITMAP(stat_used, ZX279133_FAST_STAT_DEPTH);
	u32 ddr_entries;
	u16 fast_full_zcam_used;
};

static const struct rhashtable_params zx279133_flow_key_ht_params = {
	.head_offset = offsetof(struct zx279133_flow_entry, key_node),
	.key_offset = offsetof(struct zx279133_flow_entry, key),
	.key_len = ZX279133_FAST_KEY_SIZE,
	.automatic_shrinking = true,
};

static const struct rhashtable_params zx279133_flow_reply_ht_params = {
	.head_offset = offsetof(struct zx279133_flow_entry, reply_node),
	.key_offset = offsetof(struct zx279133_flow_entry, reply_key),
	.key_len = ZX279133_FAST_KEY_SIZE,
	.automatic_shrinking = true,
};

struct zx279133_flow_block {
	struct zx279133_flow_offload *offload;
	struct net_device *ndev;
	/* The flowtable's block; it is shared by all its devices. */
	struct flow_block *fblock;
};

static unsigned int zx279133_zcam_slot(u8 zblock, u8 zcell, u8 zaddr)
{
	return (zblock * ZX279133_ZCAM_CELLS + zcell) *
		ZX279133_ZCAM_ADDRS + zaddr;
}

static int zx279133_fast_index_alloc(unsigned long *used,
				      unsigned int depth, u16 *index)
{
	unsigned long bit;

	bit = find_first_zero_bit(used, depth);
	if (bit == depth)
		return -ENOSPC;
	__set_bit(bit, used);
	*index = bit;

	return 0;
}

static int
zx279133_fast_age_alloc(struct zx279133_flow_offload *offload, u32 *index)
{
	unsigned long bit;

	bit = find_first_zero_bit(offload->age_used, ZX279133_FAST_AGE_DEPTH);
	if (bit == ZX279133_FAST_AGE_DEPTH)
		return -ENOSPC;
	__set_bit(bit, offload->age_used);
	*index = bit;

	return 0;
}

static u16 zx279133_crc16(u16 poly, const u8 *data, size_t len)
{
	u16 crc = 0;
	int bit;

	while (len--) {
		crc ^= (u16)data[len] << 8;
		for (bit = 0; bit < 8; bit++)
			crc = crc & BIT(15) ? (crc << 1) ^ poly : crc << 1;
	}

	return crc;
}

static u32 zx279133_crc32(u32 poly, const u8 *data, size_t len)
{
	u32 crc = 0;
	int bit;

	while (len--) {
		crc ^= (u32)data[len] << 24;
		for (bit = 0; bit < 8; bit++)
			crc = crc & BIT(31) ? (crc << 1) ^ poly : crc << 1;
	}

	return crc;
}

static int zx279133_zcam_alloc(struct zx279133_flow_offload *offload,
			       const u8 *key, u8 selector, u8 *zblock,
			       u8 *zcell, u8 *zaddr)
{
	u8 hash_input[49] = {};
	unsigned int slot;
	u16 crc;
	int block, cell;

	/* np.ko hashes the SDT selector, key, and zero response tail. */
	hash_input[0] = selector;
	memcpy(hash_input + 1, key, ZX279133_FAST_KEY_SIZE);
	for (block = 0; block < ZX279133_ZCAM_BLOCKS; block++) {
		crc = zx279133_crc16(block & 1 ? 0x8005 : 0x1021,
				     hash_input, sizeof(hash_input));
		for (cell = 0; cell < ZX279133_ZCAM_CELLS; cell++) {
			*zaddr = crc >> cell;
			slot = zx279133_zcam_slot(block, cell, *zaddr);
			if (test_and_set_bit(slot, offload->zcam_used))
				continue;
			*zblock = block;
			*zcell = cell;
			return 0;
		}
	}

	return -ENOSPC;
}

static int zx279133_ddr_alloc(struct zx279133_flow_offload *offload,
			      struct zx279133_flow_entry *entry, u8 selector)
{
	u8 hash_input[49] = {};
	u32 crc;

	hash_input[0] = selector;
	memcpy(hash_input + 1, entry->key, ZX279133_FAST_KEY_SIZE);
	crc = zx279133_crc32(ZX279133_HASH_CRC32_POLY, hash_input, sizeof(hash_input));
	entry->ddr_bucket = crc & (ZX279133_DDR_BUCKETS - 1);
	if (test_and_set_bit(entry->ddr_bucket, offload->ddr_used))
		return -ENOSPC;
	entry->in_ddr = true;
	if (!offload->ddr_entries)
		dev_info(offload->eth->dev,
			 "SDT14 fast-flow overflow entered DDR bulk 0 at bucket %u\n",
			 entry->ddr_bucket);
	offload->ddr_entries++;

	return 0;
}

static int zx279133_flow_slot_alloc(struct zx279133_flow_offload *offload,
				    struct zx279133_flow_entry *entry,
				    u8 selector)
{
	int ret;

	if (!entry->full ||
	    offload->fast_full_zcam_used < ZX279133_FAST_FULL_ZCAM_LIMIT) {
		ret = zx279133_zcam_alloc(offload, entry->key, selector,
					  &entry->zblock, &entry->zcell,
					  &entry->zaddr);
		if (!ret) {
			if (entry->full)
				offload->fast_full_zcam_used++;
			return 0;
		}
		if (!entry->full)
			return ret;
	}

	ret = zx279133_ddr_alloc(offload, entry, selector);
	if (ret != -ENOSPC)
		return ret;

	/* The external table has one 512-bit slot per CRC32 result.  Resolve a
	 * collision through the ZCAM's 4 blocks x 5 CRC16 candidates.
	 */
	ret = zx279133_zcam_alloc(offload, entry->key, selector,
				  &entry->zblock, &entry->zcell, &entry->zaddr);
	if (!ret)
		offload->fast_full_zcam_used++;

	return ret;
}

static void zx279133_flow_slot_release(struct zx279133_flow_offload *offload,
				       const struct zx279133_flow_entry *entry)
{
	if (entry->in_ddr) {
		__clear_bit(entry->ddr_bucket, offload->ddr_used);
		offload->ddr_entries--;
		return;
	}

	clear_bit(zx279133_zcam_slot(entry->zblock, entry->zcell,
				     entry->zaddr), offload->zcam_used);
	if (entry->full)
		offload->fast_full_zcam_used--;
}

/*
 * As np.ko's fast_table_write(): a multi entry, whose response starts in the
 * IKEY table, while the ZCAM and the 4096 IKEYs have room, else a full SDT14
 * entry, which can overflow into the DDR bulk. The LAN entry program adds a
 * PPPoE header from either.
 */
static int zx279133_flow_storage_alloc(struct zx279133_flow_offload *offload,
				       struct zx279133_flow_entry *entry)
{
	int ret;

	if (!entry->full) {
		ret = zx279133_flow_slot_alloc(offload, entry,
					       ZX279133_FAST_MULTI_SELECTOR);
		if (!ret) {
			ret = zx279133_fast_index_alloc(offload->ikey_used,
							ZX279133_FAST_IKEY_DEPTH,
							&entry->ikey);
			if (!ret)
				return 0;
			zx279133_flow_slot_release(offload, entry);
		}
		if (ret != -ENOSPC)
			return ret;
		entry->full = true;
	}

	return zx279133_flow_slot_alloc(offload, entry,
					ZX279133_FAST_FULL_SELECTOR);
}

static int zx279133_zcam_write(struct zx279133_flow_offload *offload,
			       u8 zblock, u8 zcell, u8 zaddr,
			       const u32 *table, unsigned int words)
{
	struct zx279133_eth *eth = offload->eth;
	u32 agclk, cmd, status;
	int i, ret;

	agclk = readl(eth->pps_base + ZX279133_PPU_AGCLK_CFG);
	if (agclk & ZX279133_SE_HASH_AGCLK)
		writel(agclk & ~ZX279133_SE_HASH_AGCLK,
		       eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	ret = readl_poll_timeout_atomic(eth->pps_base + ZX279133_SE_ALG_WR_DONE,
					status, status & BIT(0), 1, 1000);
	if (ret)
		goto out_restore_clock;

	for (i = 0; i < words; i++)
		writel(table[i], eth->pps_base + ZX279133_SE_ALG_WR_DATA + 4 * i);

	cmd = zaddr | (zblock >> 1) << 12 | (zblock & 1) << 11 |
		zcell << 8 | ((BIT(words / 4) - 1) & 0xf) << 16;
	writel(cmd, eth->pps_base + ZX279133_SE_ALG_WR_CMD);

out_restore_clock:
	if (agclk & ZX279133_SE_HASH_AGCLK)
		writel(agclk, eth->pps_base + ZX279133_PPU_AGCLK_CFG);

	return ret;
}

static void zx279133_host_entry_build(u32 *w, const u8 *mac, u16 port)
{
	/* Response: logical port in bits 33:28; VID and rate-limit slot 0. */
	w[0] = (port & 0xf) << 28;
	/* Key: the MAC least-significant byte first, then VID 0. */
	w[1] = (port >> 4 & 0x3) | (u32)mac[5] << 24;
	w[2] = mac[4] | mac[3] << 8 | mac[2] << 16 | (u32)mac[1] << 24;
	w[3] = mac[0] | ZX279133_HOST_FLAGS;
}

static struct zx279133_flow_host *
zx279133_host_get(struct zx279133_flow_offload *offload, const u8 *mac,
		  u16 port)
{
	struct zx279133_flow_host *host;
	u8 key[ZX279133_FAST_KEY_SIZE] = {};
	u32 w[4];
	int i;

	list_for_each_entry(host, &offload->hosts, node) {
		if (!ether_addr_equal(host->mac, mac))
			continue;
		/* The host has moved to another LAN port. */
		if (host->port != port) {
			zx279133_host_entry_build(w, mac, port);
			if (zx279133_zcam_write(offload, host->zblock,
						host->zcell, host->zaddr, w,
						ARRAY_SIZE(w)))
				return NULL;
			host->port = port;
		}
		host->users++;
		return host;
	}

	host = kzalloc(sizeof(*host), GFP_KERNEL);
	if (!host)
		return NULL;
	/* The hash input is the selector, then the key: MAC LSB first, VID. */
	for (i = 0; i < ETH_ALEN; i++)
		key[i] = mac[ETH_ALEN - 1 - i];
	if (zx279133_zcam_alloc(offload, key, ZX279133_HOST_SELECTOR,
				&host->zblock, &host->zcell, &host->zaddr))
		goto err_free;
	zx279133_host_entry_build(w, mac, port);
	if (zx279133_zcam_write(offload, host->zblock, host->zcell,
				host->zaddr, w, ARRAY_SIZE(w)))
		goto err_release;
	ether_addr_copy(host->mac, mac);
	host->port = port;
	host->users = 1;
	list_add(&host->node, &offload->hosts);
	return host;

err_release:
	clear_bit(zx279133_zcam_slot(host->zblock, host->zcell, host->zaddr),
		  offload->zcam_used);
err_free:
	kfree(host);
	return NULL;
}

static void zx279133_host_free(struct zx279133_flow_offload *offload,
			       struct zx279133_flow_host *host,
			       bool clear_hardware)
{
	u32 w[4] = {};

	if (clear_hardware &&
	    zx279133_zcam_write(offload, host->zblock, host->zcell,
				host->zaddr, w, ARRAY_SIZE(w)))
		dev_warn(offload->eth->dev, "failed to clear host %pM\n",
			 host->mac);
	clear_bit(zx279133_zcam_slot(host->zblock, host->zcell, host->zaddr),
		  offload->zcam_used);
	list_del(&host->node);
	kfree(host);
}

static void zx279133_host_put(struct zx279133_flow_offload *offload,
			      struct zx279133_flow_host *host)
{
	if (host && !--host->users)
		zx279133_host_free(offload, host, true);
}

static void zx279133_ddr_write(struct zx279133_flow_offload *offload,
			       u32 bucket, const u32 *table)
{
	u8 ddr_table[ZX279133_DDR_BUCKET_SIZE];
	u8 __iomem *dst = offload->eth->se_hash_mem +
			  (bucket << ZX279133_DDR_BUCKET_SHIFT);
	const u8 *src = (const u8 *)table;
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(ddr_table); i++)
		ddr_table[i] = src[ARRAY_SIZE(ddr_table) - 1 - i];
	ddr_table[0] &= ~BIT(7);
	memcpy_toio(dst, ddr_table, sizeof(ddr_table));
	/* Publish the complete body before making the table valid. */
	wmb();
	writeb(ddr_table[0] | BIT(7), dst);
	/* Complete the valid-bit write before reporting the rule in hardware. */
	wmb();
}

static void zx279133_ddr_clear(struct zx279133_flow_offload *offload,
			       u32 bucket)
{
	u8 __iomem *dst = offload->eth->se_hash_mem +
			  (bucket << ZX279133_DDR_BUCKET_SHIFT);

	writeb(0, dst);
	/* Invalidate the table before clearing data that SE may still read. */
	wmb();
	memset_io(dst + 1, 0, ZX279133_DDR_BUCKET_SIZE - 1);
	/* Complete the clear before returning the bucket to the allocator. */
	wmb();
}

static int zx279133_flow_table_write(struct zx279133_flow_offload *offload,
				     const struct zx279133_flow_entry *entry,
				     const u32 *table, unsigned int words)
{
	if (entry->in_ddr) {
		zx279133_ddr_write(offload, entry->ddr_bucket, table);
		return 0;
	}

	return zx279133_zcam_write(offload, entry->zblock, entry->zcell,
				    entry->zaddr, table, words);
}

static void zx279133_put_ipv4(u8 *dst, __be32 addr)
{
	put_unaligned(be32_to_cpu(addr), (u32 *)dst);
}

static void zx279133_fast_key_build(u8 *key,
				    const struct zx279133_flow_tuple *tuple,
				    u8 ip_proto)
{
	memset(key, 0, ZX279133_FAST_KEY_SIZE);
	put_unaligned(ntohs(tuple->dst_port), (u16 *)(key + 3));
	put_unaligned(ntohs(tuple->src_port), (u16 *)(key + 5));
	zx279133_put_ipv4(key + 7, tuple->dst_addr);
	zx279133_put_ipv4(key + 11, tuple->src_addr);
	key[15] = ip_proto;
}

#define ZX279133_IPV6_CRC_POLY		0x04c11db7

static u32 zx279133_ipv6_crc32(u32 crc, const u8 *data, size_t len)
{
	int bit;

	while (len--) {
		crc ^= (u32)*data++ << 24;
		for (bit = 0; bit < 8; bit++)
			crc = crc & BIT(31) ?
				(crc << 1) ^ ZX279133_IPV6_CRC_POLY : crc << 1;
	}

	return crc;
}

static void zx279133_fast_key_build_v6(u8 *key,
				       const struct zx279133_flow_data *data,
				       u8 ip_proto)
{
	u32 crc;

	memset(key, 0, ZX279133_FAST_KEY_SIZE);
	put_unaligned(ntohs(data->tuple.dst_port), (u16 *)(key + 3));
	put_unaligned(ntohs(data->tuple.src_port), (u16 *)(key + 5));

	/* SPA full-tuple mode covers destination then source for DIP32 and
	 * source then destination for SIP32, using big-endian CRC-32.
	 */
	crc = zx279133_ipv6_crc32(0, data->dst_v6.s6_addr, sizeof(data->dst_v6));
	crc = zx279133_ipv6_crc32(crc, data->src_v6.s6_addr, sizeof(data->src_v6));
	put_unaligned(crc, (u32 *)(key + 7));
	crc = zx279133_ipv6_crc32(0, data->src_v6.s6_addr, sizeof(data->src_v6));
	crc = zx279133_ipv6_crc32(crc, data->dst_v6.s6_addr, sizeof(data->dst_v6));
	put_unaligned(crc, (u32 *)(key + 11));
	key[15] = ip_proto;
}

/*
 * The key the reply of an upstream flow is looked up with: the translated
 * tuple with source and destination exchanged.
 */
static void zx279133_fast_reply_key_build(u8 *key,
					  const struct zx279133_flow_data *data,
					  const struct zx279133_flow_tuple *xlate,
					  u8 ip_proto, bool ipv6)
{
	struct zx279133_flow_data reply = {};

	reply.tuple.src_addr = xlate->dst_addr;
	reply.tuple.dst_addr = xlate->src_addr;
	reply.tuple.src_port = xlate->dst_port;
	reply.tuple.dst_port = xlate->src_port;
	if (ipv6) {
		reply.src_v6 = data->dst_v6;
		reply.dst_v6 = data->src_v6;
		zx279133_fast_key_build_v6(key, &reply, ip_proto);
	} else {
		zx279133_fast_key_build(key, &reply.tuple, ip_proto);
	}
}

static void zx279133_fast_response_build(u8 *fast,
					 const struct zx279133_flow_data *data,
					 const struct zx279133_flow_tuple *xlate,
					 enum zx279133_flow_port ingress,
					 enum zx279133_flow_port egress,
					 u16 gemport, u16 ikey, u16 stat,
					 bool has_stat, bool pppoe, u8 wanid)
{
	int i;

	/* The response stores MAC addresses and IPv4 values least-significant
	 * byte first, exactly as np.ko's fast_hashinfo_set() emits them.
	 */
	memset(fast, 0, ZX279133_FAST_DATA_SIZE);
	if (pppoe)
		put_unaligned(4, (u32 *)(fast + 16));
	put_unaligned(stat, (u16 *)(fast + 2));
	for (i = 0; i < ETH_ALEN; i++)
		fast[8 + i] = data->eth.h_dest[ETH_ALEN - 1 - i];

	put_unaligned(gemport, (u16 *)(fast + 20));
	/* Bits 4:0 select the WANID entry that supplies the source MAC. */
	fast[23] = wanid;
	put_unaligned(ikey, (u16 *)(fast + 26));
	if (has_stat)
		fast[29] |= BIT(4);
	fast[30] = BIT(5) | BIT(4) | BIT(3);
	if (ingress == ZX279133_FLOW_PORT_LAN) {
		/*
		 * The LAN entry program has already removed the RTL8372N
		 * transport tag, so the frame keeps its VLAN action and only a
		 * PPPoE push changes its length (eight bytes, stored halved).
		 * wan_mode (bit 1) sends it out the WAN path, where the push
		 * takes the session from the WANID entry and the frame leaves
		 * by the port at byte 20, a LAN port too. Without it the egress
		 * port is cleared and the frame is lost, whichever port it is
		 * for.
		 */
		put_unaligned(pppoe ? 4 : 0, (u32 *)(fast + 16));
		fast[29] |= BIT(1);
	}

	if (xlate->src_addr != data->tuple.src_addr)
		fast[29] |= BIT(7);
	if (xlate->dst_addr != data->tuple.dst_addr) {
		zx279133_put_ipv4(fast + 4, xlate->dst_addr);
		fast[30] |= BIT(0);
	}
	/*
	 * The port on the WAN side is always written, as np.ko does. Between
	 * two LAN ports either side may be translated, when one of them is a
	 * second WAN.
	 */
	if (ingress == ZX279133_FLOW_PORT_LAN ||
	    xlate->src_port != data->tuple.src_port) {
		put_unaligned(ntohs(xlate->src_port), (u16 *)fast);
		fast[30] |= BIT(1);
	}
	if (ingress == ZX279133_FLOW_PORT_WAN ||
	    xlate->dst_port != data->tuple.dst_port) {
		put_unaligned(ntohs(xlate->dst_port), (u16 *)(fast + 14));
		fast[30] |= BIT(2);
	}
}

static void zx279133_fast_multi_table_build(u32 *table, const u8 *fast,
					    const u8 *key, u32 age)
{
	u8 packed[18] = {};
	u8 *bytes = (u8 *)table;
	u32 tail;
	int i;

	/* fast_table_write() packs response bytes 17..31 around the age index. */
	for (i = 0; i < 14; i++) {
		packed[i] |= (fast[17 + i] & 0x3) << 6;
		packed[i + 1] |= fast[17 + i] >> 2;
	}
	tail = (u32)age << 8 | (fast[31] & 0x3) << 6 |
	       (u32)(fast[31] & ~0x3) << 24;
	packed[14] |= tail;
	packed[15] = tail >> 8;
	packed[16] = tail >> 16;
	packed[17] = tail >> 24;

	memset(table, 0, ZX279133_ZCAM_ENTRY_SIZE / 2);
	memcpy(bytes, packed + 3, 15);
	memcpy(bytes + 15, key, ZX279133_FAST_KEY_SIZE);
	bytes[31] = ZX279133_FAST_MULTI_FOOTER;
}

static void zx279133_fast_full_table_build(u32 *table, const u8 *fast,
					   const u8 *key, u32 age)
{
	u32 response[ZX279133_FAST_DATA_SIZE / sizeof(u32)];
	u8 packed[ZX279133_FAST_DATA_SIZE + 3] = {};
	u8 *bytes = (u8 *)table;
	u8 *response_bytes = (u8 *)response;
	unsigned int i;
	u32 tail;

	/* fast_table_write() applies this conversion and the 18-bit age prefix
	 * before its age-enabled SDT14 fallback.
	 */
	memcpy(response, fast, sizeof(response));
	swap(response[0], response[4]);
	response_bytes[26] = 0;
	response_bytes[27] &= 0xe0;
	for (i = 0; i < ZX279133_FAST_DATA_SIZE - 1; i++) {
		packed[i] |= (response_bytes[i] & 0x3) << 6;
		packed[i + 1] |= response_bytes[i] >> 2;
	}
	tail = (age & GENMASK(17, 0)) << 13 |
	       (response_bytes[31] & GENMASK(6, 0)) << 6;
	for (i = 0; i < sizeof(tail); i++)
		packed[31 + i] += tail >> (8 * i);
	memset(table, 0, ZX279133_ZCAM_ENTRY_SIZE);
	memcpy(bytes + 15, packed + 3, ZX279133_FAST_DATA_SIZE);
	memcpy(bytes + 47, key, ZX279133_FAST_KEY_SIZE);
	bytes[63] = ZX279133_FAST_FULL_FOOTER;
}

static void zx279133_flow_mangle_eth(const struct flow_action_entry *act,
				     struct ethhdr *eth)
{
	u8 *dest = (u8 *)eth + act->mangle.offset;
	const u8 *src = (const u8 *)&act->mangle.val;

	if (act->mangle.offset > 8)
		return;
	if (act->mangle.mask == 0xffff) {
		src += 2;
		dest += 2;
	}
	memcpy(dest, src, act->mangle.mask ? 2 : 4);
}

static int zx279133_flow_mangle_ports(const struct flow_action_entry *act,
				      struct zx279133_flow_tuple *tuple)
{
	u32 val = ntohl(act->mangle.val);

	switch (act->mangle.offset) {
	case 0:
		if (act->mangle.mask == ~htonl(0xffff))
			tuple->dst_port = cpu_to_be16(val);
		else
			tuple->src_port = cpu_to_be16(val >> 16);
		break;
	case 2:
		tuple->dst_port = cpu_to_be16(val);
		break;
	default:
		return -EOPNOTSUPP;
	}

	return 0;
}

static int zx279133_flow_mangle_ipv4(const struct flow_action_entry *act,
				     struct zx279133_flow_tuple *tuple)
{
	__be32 *addr;

	switch (act->mangle.offset) {
	case offsetof(struct iphdr, saddr):
		addr = &tuple->src_addr;
		break;
	case offsetof(struct iphdr, daddr):
		addr = &tuple->dst_addr;
		break;
	default:
		return -EOPNOTSUPP;
	}
	memcpy(addr, &act->mangle.val, sizeof(*addr));

	return 0;
}

static int zx279133_flow_lan_vid(struct zx279133_eth *eth,
				 struct net_device *dev, u16 *vid)
{
#if IS_REACHABLE(CONFIG_NET_DSA)
	struct dsa_port *dp = dsa_port_from_netdev(dev);

	if (IS_ERR(dp) || dsa_port_to_conduit(dp) != eth->lan_ndev ||
	    dp->index < ZX279133_RTL8372N_USER_PORT_MIN ||
	    dp->index > ZX279133_RTL8372N_USER_PORT_MAX)
		return -EOPNOTSUPP;

	*vid = ZX279133_RTL8372N_TRANSPORT_VID_BASE + dp->index;
	return 0;
#else
	return -EOPNOTSUPP;
#endif
}

/*
 * Where a device is in the data path, and the port its frames leave by: the
 * WAN port, or the transport VID of a LAN port. A LAN port switched to
 * WAN-type, a second WAN, counts as WAN; 0 when a LAN device is not one port.
 */
static enum zx279133_flow_port
zx279133_flow_dev_port(struct zx279133_eth *eth, struct net_device *dev,
		       u16 *gemport)
{
	unsigned long wan_vports = READ_ONCE(eth->lan_wan_vports);
	u16 vid = 0;

	if (!dev)
		return ZX279133_FLOW_PORT_NONE;
	if (dev == eth->ndev) {
		*gemport = zx279133_tx_port;
		return ZX279133_FLOW_PORT_WAN;
	}
	if (!eth->lan_ndev ||
	    (dev != eth->lan_ndev &&
	     dev_get_iflink(dev) != eth->lan_ndev->ifindex))
		return ZX279133_FLOW_PORT_NONE;
	if (zx279133_flow_lan_vid(eth, dev, &vid))
		vid = 0;
	*gemport = vid;
	if (vid && wan_vports & BIT(vid - ZX279133_LAN_TRANSPORT_VID_MIN))
		return ZX279133_FLOW_PORT_WAN;
	return ZX279133_FLOW_PORT_LAN;
}

static int
zx279133_flow_parse(struct zx279133_flow_offload *offload,
		    struct net_device *bind_dev, struct flow_cls_offload *f,
		    struct zx279133_flow_data *data,
		    struct zx279133_flow_tuple *xlate,
		    enum zx279133_flow_port *ingress,
		    enum zx279133_flow_port *egress, u16 *gemport,
		    u8 *ip_proto, u16 *pppoe_sid, bool *ipv6)
{
	struct flow_rule *rule = flow_cls_offload_flow_rule(f);
	struct flow_match_ipv4_addrs addrs;
	struct flow_match_ipv6_addrs addrs6;
	struct flow_match_control control;
	struct flow_match_basic basic;
	struct flow_match_ports ports;
	struct flow_match_meta meta;
	struct flow_action_entry *act;
	struct net_device *idev;
	struct net_device *odev = NULL;
	u16 igemport;
	int i, ret;

	if (!flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_CONTROL) ||
	    !flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_BASIC) ||
	    (!flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_IPV4_ADDRS) &&
	     !flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_IPV6_ADDRS)) ||
	    !flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_PORTS))
		return -EOPNOTSUPP;

	idev = bind_dev;
	if (flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_META)) {
		flow_rule_match_meta(rule, &meta);
		rcu_read_lock();
		idev = dev_get_by_index_rcu(dev_net(bind_dev),
					    meta.key->ingress_ifindex);
		*ingress = zx279133_flow_dev_port(offload->eth, idev,
						  &igemport);
		rcu_read_unlock();
	} else {
		*ingress = zx279133_flow_dev_port(offload->eth, idev,
						  &igemport);
	}
	if (*ingress == ZX279133_FLOW_PORT_NONE)
		return -EOPNOTSUPP;

	flow_rule_match_control(rule, &control);
	if (flow_rule_has_control_flags(control.mask->flags, f->common.extack))
		return -EOPNOTSUPP;
	switch (control.key->addr_type) {
	case FLOW_DISSECTOR_KEY_IPV4_ADDRS:
		*ipv6 = false;
		break;
	case FLOW_DISSECTOR_KEY_IPV6_ADDRS:
		*ipv6 = true;
		break;
	default:
		return -EOPNOTSUPP;
	}

	flow_rule_match_basic(rule, &basic);
	if (basic.key->n_proto != htons(*ipv6 ? ETH_P_IPV6 : ETH_P_IP) ||
	    basic.mask->n_proto != htons(0xffff) ||
	    basic.mask->ip_proto != 0xff ||
	    (basic.key->ip_proto != IPPROTO_TCP &&
	     basic.key->ip_proto != IPPROTO_UDP))
		return -EOPNOTSUPP;
	*ip_proto = basic.key->ip_proto;

	if (*ipv6) {
		flow_rule_match_ipv6_addrs(rule, &addrs6);
		if (memchr_inv(&addrs6.mask->src, 0xff,
			       sizeof(addrs6.mask->src)) ||
		    memchr_inv(&addrs6.mask->dst, 0xff,
			       sizeof(addrs6.mask->dst)))
			return -EOPNOTSUPP;
		data->src_v6 = addrs6.key->src;
		data->dst_v6 = addrs6.key->dst;
	} else {
		flow_rule_match_ipv4_addrs(rule, &addrs);
		if (addrs.mask->src != htonl(~0U) ||
		    addrs.mask->dst != htonl(~0U))
			return -EOPNOTSUPP;
		data->tuple.src_addr = addrs.key->src;
		data->tuple.dst_addr = addrs.key->dst;
	}

	flow_rule_match_ports(rule, &ports);
	if (ports.mask->src != htons(0xffff) ||
	    ports.mask->dst != htons(0xffff))
		return -EOPNOTSUPP;
	data->tuple.src_port = ports.key->src;
	data->tuple.dst_port = ports.key->dst;
	*xlate = data->tuple;

	flow_action_for_each(i, act, &rule->action) {
		switch (act->id) {
		case FLOW_ACTION_MANGLE:
			if (act->mangle.htype == FLOW_ACT_MANGLE_HDR_TYPE_ETH)
				zx279133_flow_mangle_eth(act, &data->eth);
			break;
		case FLOW_ACTION_REDIRECT:
			odev = act->dev;
			break;
		case FLOW_ACTION_CSUM:
			break;
		case FLOW_ACTION_PPPOE_PUSH:
			if (*pppoe_sid || !act->pppoe.sid)
				return -EOPNOTSUPP;
			*pppoe_sid = act->pppoe.sid;
			break;
		default:
			return -EOPNOTSUPP;
		}
	}

	if (!odev || !is_valid_ether_addr(data->eth.h_source) ||
	    !is_valid_ether_addr(data->eth.h_dest))
		return -EOPNOTSUPP;
	/*
	 * Besides LAN <-> WAN, a flow may be routed between two LAN ports,
	 * as when one of them serves as a second WAN.
	 */
	*egress = zx279133_flow_dev_port(offload->eth, odev, gemport);
	if (*egress == ZX279133_FLOW_PORT_NONE || !*gemport ||
	    (*ingress == ZX279133_FLOW_PORT_WAN &&
	     *egress == ZX279133_FLOW_PORT_WAN))
		return -EOPNOTSUPP;
	if (*pppoe_sid && *ingress != ZX279133_FLOW_PORT_LAN)
		return -EOPNOTSUPP;

	flow_action_for_each(i, act, &rule->action) {
		if (act->id != FLOW_ACTION_MANGLE)
			continue;
		switch (act->mangle.htype) {
		case FLOW_ACT_MANGLE_HDR_TYPE_ETH:
			ret = 0;
			break;
		case FLOW_ACT_MANGLE_HDR_TYPE_IP4:
			ret = *ipv6 ? -EOPNOTSUPP :
				zx279133_flow_mangle_ipv4(act, xlate);
			break;
		case FLOW_ACT_MANGLE_HDR_TYPE_TCP:
		case FLOW_ACT_MANGLE_HDR_TYPE_UDP:
			ret = zx279133_flow_mangle_ports(act, xlate);
			break;
		default:
			ret = -EOPNOTSUPP;
		}
		if (ret)
			return ret;
	}

	return 0;
}

static bool zx279133_flow_key_exists(struct zx279133_flow_offload *offload,
				     const u8 *key)
{
	return rhashtable_lookup_fast(&offload->key_flows, key,
				      zx279133_flow_key_ht_params);
}

static int zx279133_flow_replace(struct zx279133_flow_offload *offload,
				 struct zx279133_flow_block *block,
				 struct flow_cls_offload *f)
{
	struct net_device *bind_dev = block->ndev;
	struct zx279133_wanid_profile profile = {};
	struct zx279133_flow_tuple xlate;
	struct zx279133_flow_data data = {};
	struct zx279133_flow_entry *entry, *peer;
	enum zx279133_flow_port ingress, egress;
	u8 fast[ZX279133_FAST_DATA_SIZE];
	u32 ikey[ZX279133_FAST_IKEY_SIZE / sizeof(u32)];
	u32 table[ZX279133_ZCAM_ENTRY_SIZE / sizeof(u32)];
	bool ipv6, pppoe, full, snat;
	unsigned int table_words;
	u16 gemport, pppoe_sid = 0;
	u8 ip_proto;
	int ret;

	ret = zx279133_flow_parse(offload, bind_dev, f, &data, &xlate,
				  &ingress, &egress, &gemport, &ip_proto,
				  &pppoe_sid, &ipv6);
	if (ret)
		return ret;

	entry = kzalloc(sizeof(*entry), GFP_KERNEL);
	if (!entry)
		return -ENOMEM;
	if (ipv6)
		zx279133_fast_key_build_v6(entry->key, &data, ip_proto);
	else
		zx279133_fast_key_build(entry->key, &data.tuple, ip_proto);
	snat = !ipv6 && xlate.src_addr != data.tuple.src_addr;
	entry->snat = snat;
	entry->fblock = block->fblock;
	entry->upstream = ingress == ZX279133_FLOW_PORT_LAN;
	entry->lan_routed = ingress == ZX279133_FLOW_PORT_LAN &&
			    egress == ZX279133_FLOW_PORT_LAN;
	if (entry->upstream)
		zx279133_fast_reply_key_build(entry->reply_key, &data, &xlate,
					      ip_proto, ipv6);

	mutex_lock(&offload->eth->datapath_lock);
	mutex_lock(&offload->lock);
	if (!READ_ONCE(offload->eth->hardware_prepared)) {
		ret = -ENETDOWN;
		goto out_free;
	}
	peer = xa_load(&offload->flows, f->cookie);
	if (peer) {
		ret = peer->refused ? -EOPNOTSUPP : 0;
		goto out_free;
	}
	/*
	 * The hardware adds a PPPoE header only on the way out of the WAN
	 * port: an entry for a session on a LAN port, a second WAN, is never
	 * hit. Refuse it, but remember the flow, so that its other half,
	 * which comes in PPPoE, is refused too.
	 */
	if (entry->lan_routed && pppoe_sid) {
		entry->refused = true;
		entry->pppoe = true;
		ret = xa_err(xa_store(&offload->flows, f->cookie, entry,
				      GFP_KERNEL));
		if (ret)
			goto out_free;
		entry->reply_indexed =
			!rhashtable_lookup_insert_fast(&offload->reply_flows,
						       &entry->reply_node,
						       zx279133_flow_reply_ht_params);
		entry = NULL;
		ret = -EOPNOTSUPP;
		goto out_free;
	}
	if (zx279133_flow_key_exists(offload, entry->key)) {
		ret = -EEXIST;
		goto out_free;
	}
	if (entry->upstream) {
		pppoe = pppoe_sid;
		/*
		 * The LAN entry program does not take a PPPoE header off
		 * either: the frames of a PPPoE session on a LAN port miss the
		 * flow table and reach the CPU. Their rule does not say it, but
		 * the refused rule of the other half does.
		 */
		peer = entry->lan_routed && !pppoe ?
			rhashtable_lookup_fast(&offload->reply_flows,
					       entry->key,
					       zx279133_flow_reply_ht_params) :
			NULL;
		if (peer && peer->pppoe) {
			ret = -EOPNOTSUPP;
			goto out_free;
		}
	} else {
		/*
		 * A downstream rule does not say whether its packets arrive
		 * in PPPoE; the upstream rule of the same connection does,
		 * by pushing it. Take the answer from that rule, and wait
		 * while it is not in hardware yet: nf_flow_table offers this
		 * rule again once its packets take the software path, by
		 * then after the upstream half. Guessing instead breaks a
		 * connection opened from the WAN, whose downstream half
		 * comes first: the hardware matches PPPoE frames by the inner
		 * tuple and would pass them to the LAN still encapsulated.
		 */
		peer = rhashtable_lookup_fast(&offload->reply_flows, entry->key,
					      zx279133_flow_reply_ht_params);
		if (!peer) {
			ret = -EAGAIN;
			goto out_free;
		}
		pppoe = peer->pppoe;
	}
	/* PPPoE pop stays out of the finite IKEY table in a full entry. */
	entry->pppoe = pppoe;
	entry->full = pppoe && !entry->upstream;
	entry->has_age = true;
	ret = zx279133_flow_storage_alloc(offload, entry);
	if (ret)
		goto out_free;
	full = entry->full;
	ret = zx279133_fast_age_alloc(offload, &entry->age);
	if (ret)
		goto out_release_storage;
	ret = zx279133_fast_index_alloc(offload->stat_used,
					ZX279133_FAST_STAT_DEPTH, &entry->stat);
	if (!ret) {
		entry->has_stat = true;
		ret = zx279133_fast_stats_read(offload->eth, entry->stat,
					       &entry->packets, &entry->bytes);
		if (ret)
			goto out_release_stat;
	} else if (ret != -ENOSPC) {
		goto out_release_age;
	}
	ether_addr_copy(profile.mac, data.eth.h_source);
	if (snat)
		profile.sip = be32_to_cpu(xlate.src_addr);
	if (pppoe && entry->upstream) {
		profile.pppoe = ZX279133_WANID_PPPOE_PUSH;
		profile.sid = pppoe_sid;
	} else if (pppoe) {
		profile.pppoe = ZX279133_WANID_PPPOE_POP;
	}
	ret = zx279133_wanid_get(offload->eth, !entry->upstream, &profile,
				 &entry->wanid);
	if (ret)
		goto out_release_stat;
	zx279133_fast_response_build(fast, &data, &xlate, ingress, egress,
				     gemport, entry->ikey, entry->stat,
				     entry->has_stat, pppoe, entry->wanid);
	if (full) {
		zx279133_fast_full_table_build(table, fast, entry->key,
					       entry->age);
	} else {
		memcpy(ikey, fast, sizeof(ikey));
		zx279133_fast_multi_table_build(table, fast, entry->key, entry->age);
		ret = zx279133_fast_ikey_write(offload->eth, entry->ikey, ikey);
		if (ret)
			goto out_put_wanid;
	}
	ret = xa_err(xa_store(&offload->flows, f->cookie, entry, GFP_KERNEL));
	if (ret)
		goto out_clear_ikey;
	ret = rhashtable_insert_fast(&offload->key_flows, &entry->key_node,
				     zx279133_flow_key_ht_params);
	if (ret) {
		xa_erase(&offload->flows, f->cookie);
		goto out_clear_ikey;
	}
	table_words = full ? ARRAY_SIZE(table) : ARRAY_SIZE(ikey) * 2;
	ret = zx279133_flow_table_write(offload, entry, table, table_words);
	if (ret) {
		rhashtable_remove_fast(&offload->key_flows, &entry->key_node,
				       zx279133_flow_key_ht_params);
		xa_erase(&offload->flows, f->cookie);
		goto out_clear_ikey;
	}
	/* An unindexed entry only keeps its reply in software. */
	if (entry->upstream)
		entry->reply_indexed =
			!rhashtable_lookup_insert_fast(&offload->reply_flows,
						       &entry->reply_node,
						       zx279133_flow_reply_ht_params);
	/*
	 * A flow out of a LAN port tells where its host is: give the host the
	 * SDT 9 entry its frames need to reach the flow table, both for a LAN
	 * client and for the gateway behind a LAN port used as a second WAN.
	 * Without one they still work, through the CPU.
	 */
	if (egress == ZX279133_FLOW_PORT_LAN) {
		entry->host = zx279133_host_get(offload, data.eth.h_dest,
						gemport);
		if (!entry->host)
			dev_warn_ratelimited(offload->eth->dev,
					     "no MAC table entry for %pM\n",
					     data.eth.h_dest);
	}
	mutex_unlock(&offload->lock);
	mutex_unlock(&offload->eth->datapath_lock);
	return 0;

out_clear_ikey:
	if (!full) {
		memset(ikey, 0, sizeof(ikey));
		zx279133_fast_ikey_write(offload->eth, entry->ikey, ikey);
	}
out_put_wanid:
	zx279133_wanid_put(offload->eth, entry->wanid, snat);
out_release_stat:
	if (entry->has_stat)
		__clear_bit(entry->stat, offload->stat_used);
out_release_age:
	__clear_bit(entry->age, offload->age_used);
out_release_storage:
	if (!full)
		__clear_bit(entry->ikey, offload->ikey_used);
	zx279133_flow_slot_release(offload, entry);
out_free:
	mutex_unlock(&offload->lock);
	mutex_unlock(&offload->eth->datapath_lock);
	kfree(entry);
	return ret;
}

static int
zx279133_flow_entry_hw_clear(struct zx279133_flow_offload *offload,
			     const struct zx279133_flow_entry *entry)
{
	u32 ikey[ZX279133_FAST_IKEY_SIZE / sizeof(u32)] = {};
	u32 table[ZX279133_ZCAM_ENTRY_SIZE / sizeof(u32)] = {};
	int ret;

	if (entry->in_ddr) {
		zx279133_ddr_clear(offload, entry->ddr_bucket);
		ret = 0;
	} else {
		ret = zx279133_zcam_write(offload, entry->zblock, entry->zcell,
					  entry->zaddr, table,
					  entry->full ? ARRAY_SIZE(table) :
							 ARRAY_SIZE(ikey) * 2);
	}
	if (ret)
		return ret;
	if (!entry->full) {
		ret = zx279133_fast_ikey_write(offload->eth, entry->ikey, ikey);
		if (ret)
			return ret;
	}

	return 0;
}

static void zx279133_flow_entry_unindex(struct zx279133_flow_offload *offload,
					struct zx279133_flow_entry *entry)
{
	if (entry->reply_indexed)
		rhashtable_remove_fast(&offload->reply_flows,
				       &entry->reply_node,
				       zx279133_flow_reply_ht_params);
}

/* Take one entry out of the hardware and the driver; offload->lock held. */
static int zx279133_flow_entry_remove(struct zx279133_flow_offload *offload,
				      struct zx279133_flow_entry *entry,
				      unsigned long cookie)
{
	int ret;

	if (entry->refused) {
		zx279133_flow_entry_unindex(offload, entry);
		xa_erase(&offload->flows, cookie);
		kfree(entry);
		return 0;
	}
	ret = zx279133_flow_entry_hw_clear(offload, entry);
	if (ret)
		return ret;
	rhashtable_remove_fast(&offload->key_flows, &entry->key_node,
			       zx279133_flow_key_ht_params);
	zx279133_flow_entry_unindex(offload, entry);
	xa_erase(&offload->flows, cookie);
	zx279133_flow_slot_release(offload, entry);
	if (!entry->full)
		__clear_bit(entry->ikey, offload->ikey_used);
	__clear_bit(entry->age, offload->age_used);
	if (entry->has_stat)
		__clear_bit(entry->stat, offload->stat_used);
	zx279133_wanid_put(offload->eth, entry->wanid, entry->snat);
	zx279133_host_put(offload, entry->host);
	kfree(entry);

	return 0;
}

static int zx279133_flow_destroy(struct zx279133_flow_offload *offload,
				 struct flow_cls_offload *f)
{
	struct zx279133_flow_entry *entry;
	int ret;

	mutex_lock(&offload->lock);
	entry = xa_load(&offload->flows, f->cookie);
	ret = entry ? zx279133_flow_entry_remove(offload, entry, f->cookie) :
		      -ENOENT;
	mutex_unlock(&offload->lock);
	return ret;
}

/*
 * nf_tables unbinds a flowtable from its devices before it tears its flows
 * down, so their DESTROY commands no longer reach the driver. When the last
 * device of a flowtable is unbound, remove what that flowtable left behind;
 * a firewall reload binds the new flowtable first, so the driver would
 * otherwise keep the old entries and everything they hold.
 */
static void zx279133_flow_entries_clear_block(struct zx279133_flow_offload *offload,
					      const struct flow_block *fblock)
{
	struct zx279133_flow_entry *entry;
	unsigned long index;

	mutex_lock(&offload->lock);
	xa_for_each(&offload->flows, index, entry)
		if (entry->fblock == fblock &&
		    zx279133_flow_entry_remove(offload, entry, index))
			dev_warn(offload->eth->dev,
				 "failed to clear hardware flow %lu\n", index);
	mutex_unlock(&offload->lock);
}

/*
 * A flow counter adds up the frame on the WAN side, from the MAC header
 * through the FCS, with the PPPoE header of a session: IP + 18, or IP + 26,
 * measured in both directions. nf_flow_table, like conntrack and the devices
 * it passes the counts on to, counts the IP packet.
 */
static u64 zx279133_flow_ip_bytes(const struct zx279133_flow_entry *entry,
				  u64 packets, u64 bytes)
{
	u64 frame = ETH_HLEN + ETH_FCS_LEN;

	if (entry->pppoe)
		frame += PPPOE_SES_HLEN;
	frame *= packets;

	return bytes > frame ? bytes - frame : 0;
}

static int zx279133_flow_stats(struct zx279133_flow_offload *offload,
			       struct flow_cls_offload *f)
{
	struct zx279133_flow_entry *entry;
	unsigned long lastused;
	bool used;
	u64 packets, bytes;
	int ret;

	mutex_lock(&offload->lock);
	entry = xa_load(&offload->flows, f->cookie);
	if (!entry || entry->refused) {
		ret = -ENOENT;
		goto out_unlock;
	}
	if (entry->has_age) {
		ret = zx279133_fast_age_read_clear(offload->eth, entry->age, &used);
		if (ret)
			goto out_unlock;
		if (used)
			entry->lastused = jiffies;
	}
	packets = entry->packets;
	bytes = entry->bytes;
	if (entry->has_stat) {
		ret = zx279133_fast_stats_read(offload->eth, entry->stat,
					       &packets, &bytes);
		if (ret)
			goto out_unlock;
	}
	if (!entry->has_age &&
	    (packets != entry->packets || bytes != entry->bytes))
		entry->lastused = jiffies;
	lastused = entry->lastused;
	flow_stats_update(&f->stats,
			  zx279133_flow_ip_bytes(entry,
						 packets - entry->packets,
						 bytes - entry->bytes),
			  packets - entry->packets, 0, lastused,
			  FLOW_ACTION_HW_STATS_DELAYED);
	entry->packets = packets;
	entry->bytes = bytes;

out_unlock:
	mutex_unlock(&offload->lock);
	return ret;
}

static int zx279133_flow_command(struct zx279133_flow_offload *offload,
				  struct zx279133_flow_block *block,
				  struct flow_cls_offload *f)
{
	switch (f->command) {
	case FLOW_CLS_REPLACE:
		return zx279133_flow_replace(offload, block, f);
	case FLOW_CLS_DESTROY:
		return zx279133_flow_destroy(offload, f);
	case FLOW_CLS_STATS:
		return zx279133_flow_stats(offload, f);
	default:
		return -EOPNOTSUPP;
	}
}

static int zx279133_flow_block_cb(enum tc_setup_type type, void *type_data,
				  void *cb_priv)
{
	struct zx279133_flow_block *block = cb_priv;
	struct flow_cls_offload *f = type_data;

	if (type != TC_SETUP_CLSFLOWER)
		return -EOPNOTSUPP;
	return zx279133_flow_command(block->offload, block, f);
}

static void zx279133_flow_block_release(void *cb_priv)
{
	kfree(cb_priv);
}

static void
zx279133_flow_entries_clear(struct zx279133_flow_offload *offload,
			    bool clear_hardware)
{
	struct zx279133_flow_host *host, *next;
	struct zx279133_flow_entry *entry;
	unsigned long index;

	mutex_lock(&offload->lock);
	xa_for_each(&offload->flows, index, entry) {
		if (entry->refused) {
			zx279133_flow_entry_unindex(offload, entry);
			kfree(entry);
			continue;
		}
		if (clear_hardware &&
		    zx279133_flow_entry_hw_clear(offload, entry))
			dev_warn(offload->eth->dev,
				 "failed to clear hardware flow %lu\n", index);
		zx279133_wanid_put(offload->eth, entry->wanid, entry->snat);
		rhashtable_remove_fast(&offload->key_flows, &entry->key_node,
				       zx279133_flow_key_ht_params);
		zx279133_flow_entry_unindex(offload, entry);
		kfree(entry);
	}
	list_for_each_entry_safe(host, next, &offload->hosts, node)
		zx279133_host_free(offload, host, clear_hardware);
	xa_destroy(&offload->flows);
	xa_init(&offload->flows);
	bitmap_zero(offload->zcam_used, ZX279133_ZCAM_SLOTS);
	bitmap_zero(offload->ddr_used, ZX279133_DDR_BUCKETS);
	bitmap_zero(offload->ikey_used, ZX279133_FAST_IKEY_DEPTH);
	bitmap_zero(offload->age_used, ZX279133_FAST_AGE_DEPTH);
	bitmap_zero(offload->stat_used, ZX279133_FAST_STAT_DEPTH);
	offload->fast_full_zcam_used = 0;
	offload->ddr_entries = 0;
	mutex_unlock(&offload->lock);
}

/* Whether a device of this flowtable is still bound to the driver. */
static bool zx279133_flow_block_bound(struct zx279133_flow_offload *offload,
				      const struct flow_block *fblock)
{
	struct flow_block_cb *block_cb;

	list_for_each_entry(block_cb, &offload->block_cb_list, driver_list) {
		struct zx279133_flow_block *block = block_cb->cb_priv;

		if (block->fblock == fblock)
			return true;
	}
	return false;
}

static int zx279133_flow_setup_block(struct zx279133_flow_offload *offload,
				     struct net_device *ndev,
				     struct flow_block_offload *f)
{
	struct zx279133_flow_block *block;
	struct flow_block_cb *block_cb;
	flow_setup_cb_t *cb = zx279133_flow_block_cb;

	if (!tc_can_offload(ndev) ||
	    f->binder_type != FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS)
		return -EOPNOTSUPP;
	f->driver_block_list = &offload->block_cb_list;

	switch (f->command) {
	case FLOW_BLOCK_BIND:
		block_cb = flow_block_cb_lookup(f->block, cb, ndev);
		if (block_cb) {
			flow_block_cb_incref(block_cb);
			return 0;
		}
		block = kzalloc(sizeof(*block), GFP_KERNEL);
		if (!block)
			return -ENOMEM;
		block->offload = offload;
		block->ndev = ndev;
		block->fblock = f->block;
		block_cb = flow_block_cb_alloc(cb, ndev, block,
					       zx279133_flow_block_release);
		if (IS_ERR(block_cb)) {
			kfree(block);
			return PTR_ERR(block_cb);
		}
		flow_block_cb_incref(block_cb);
		flow_block_cb_add(block_cb, f);
		list_add_tail(&block_cb->driver_list, &offload->block_cb_list);
		return 0;
	case FLOW_BLOCK_UNBIND:
		block_cb = flow_block_cb_lookup(f->block, cb, ndev);
		if (!block_cb)
			return -ENOENT;
		if (!flow_block_cb_decref(block_cb)) {
			flow_block_cb_remove(block_cb, f);
			list_del(&block_cb->driver_list);
			if (!zx279133_flow_block_bound(offload, f->block))
				zx279133_flow_entries_clear_block(offload,
								  f->block);
		}
		return 0;
	default:
		return -EOPNOTSUPP;
	}
}

int zx279133_flow_offload_setup_tc(struct zx279133_eth *eth,
				   struct net_device *ndev,
				    enum tc_setup_type type, void *type_data)
{
	if (type != TC_SETUP_BLOCK && type != TC_SETUP_FT)
		return -EOPNOTSUPP;
	return zx279133_flow_setup_block(eth->flow_offload, ndev, type_data);
}

bool zx279133_flow_offload_active(struct zx279133_eth *eth)
{
	struct zx279133_flow_offload *offload = eth->flow_offload;
	bool active;

	mutex_lock(&offload->lock);
	active = !xa_empty(&offload->flows);
	mutex_unlock(&offload->lock);
	return active;
}

void zx279133_flow_offload_flush(struct zx279133_eth *eth)
{
	zx279133_flow_entries_clear(eth->flow_offload, false);
}

/* Take every flow out of the hardware too, which stays up. */
void zx279133_flow_offload_reset(struct zx279133_eth *eth)
{
	if (eth->flow_offload)
		zx279133_flow_entries_clear(eth->flow_offload, true);
}

static struct zx279133_eth *zx279133_wan_ports_eth(struct device *dev)
{
	struct zx279133_lan_service *service = dev_get_drvdata(dev);

	return service ? container_of(service, struct zx279133_eth,
				      lan_service) : NULL;
}

/*
 * wan_ports: the LAN ports switched to WAN-type, by name. Writing
 * "<port> 1" switches one, "<port> 0" switches it back; userspace does it
 * for the ports its firewall puts in a WAN zone.
 */
static ssize_t wan_ports_show(struct device *dev,
			      struct device_attribute *attr, char *buf)
{
	struct zx279133_eth *eth = zx279133_wan_ports_eth(dev);
	unsigned long wan_vports;
	struct net_device *ndev;
	int n = 0;
	u16 vid;

	if (!eth)
		return -ENODEV;
	wan_vports = READ_ONCE(eth->lan_wan_vports);
	rcu_read_lock();
	for_each_netdev_rcu(&init_net, ndev)
		if (!zx279133_flow_lan_vid(eth, ndev, &vid) &&
		    wan_vports & BIT(vid - ZX279133_LAN_TRANSPORT_VID_MIN))
			n += sysfs_emit_at(buf, n, "%s ", ndev->name);
	rcu_read_unlock();
	n += sysfs_emit_at(buf, n, "\n");

	return n;
}

static ssize_t wan_ports_store(struct device *dev,
			       struct device_attribute *attr,
			       const char *buf, size_t count)
{
	struct zx279133_eth *eth = zx279133_wan_ports_eth(dev);
	struct net_device *ndev;
	char name[IFNAMSIZ];
	unsigned int wan;
	int ret;
	u16 vid;

	if (!eth)
		return -ENODEV;
	if (sscanf(buf, "%15s %u", name, &wan) != 2 || wan > 1)
		return -EINVAL;
	ndev = dev_get_by_name(&init_net, name);
	if (!ndev)
		return -ENODEV;
	ret = zx279133_flow_lan_vid(eth, ndev, &vid);
	/* A bridged port is a LAN port. */
	if (!ret && wan && netif_is_bridge_port(ndev))
		ret = -EBUSY;
	dev_put(ndev);
	if (ret)
		return ret == -EBUSY ? ret : -EINVAL;
	ret = zx279133_lan_vport_wan_set(eth, vid, wan);

	return ret ? ret : count;
}
static DEVICE_ATTR_RW(wan_ports);

static struct attribute *zx279133_wan_ports_attrs[] = {
	&dev_attr_wan_ports.attr,
	NULL
};

static const struct attribute_group zx279133_wan_ports_group = {
	.attrs = zx279133_wan_ports_attrs,
};

static void zx279133_flow_offload_cleanup(void *data)
{
	struct zx279133_flow_offload *offload = data;

	zx279133_flow_entries_clear(offload, false);
	rhashtable_destroy(&offload->reply_flows);
	rhashtable_destroy(&offload->key_flows);
	offload->eth->flow_offload = NULL;
}

#ifdef CONFIG_DEBUG_FS
/* One line per hardware flow, with the SDT29 counters read live. */
static int zx279133_flows_show(struct seq_file *s, void *unused)
{
	struct zx279133_flow_offload *offload = s->private;
	struct zx279133_flow_entry *entry;
	struct zx279133_flow_host *host;
	unsigned long index;
	u64 packets, bytes;

	mutex_lock(&offload->lock);
	xa_for_each(&offload->flows, index, entry) {
		if (entry->refused) {
			seq_printf(s, "%16phN lan refused pppoe\n", entry->key);
			continue;
		}
		seq_printf(s, "%16phN %s %s wanid %u pppoe %d snat %d age %u",
			   entry->key, entry->lan_routed ? "lan" :
			   entry->upstream ? "up" : "down",
			   entry->full ? "full" : "multi", entry->wanid,
			   entry->pppoe, entry->snat, entry->age);
		if (entry->in_ddr)
			seq_printf(s, " ddr %u", entry->ddr_bucket);
		else
			seq_printf(s, " zcam %u/%u/%u", entry->zblock,
				   entry->zcell, entry->zaddr);
		if (entry->has_stat &&
		    !zx279133_fast_stats_read(offload->eth, entry->stat,
					      &packets, &bytes))
			seq_printf(s, " stat %u packets %llu bytes %llu",
				   entry->stat, packets, bytes);
		seq_putc(s, '\n');
	}
	list_for_each_entry(host, &offload->hosts, node)
		seq_printf(s, "host %pM port %u zcam %u/%u/%u flows %u\n",
			   host->mac, host->port, host->zblock, host->zcell,
			   host->zaddr, host->users);
	mutex_unlock(&offload->lock);

	return 0;
}
DEFINE_SHOW_ATTRIBUTE(zx279133_flows);

static void zx279133_flows_debugfs_remove(void *data)
{
	debugfs_remove(data);
}

static int zx279133_flows_debugfs_init(struct zx279133_flow_offload *offload)
{
	struct dentry *dentry;

	dentry = debugfs_create_file("zx279133-flows", 0400, NULL, offload,
				     &zx279133_flows_fops);
	return devm_add_action_or_reset(offload->eth->dev,
					zx279133_flows_debugfs_remove, dentry);
}
#else
static int zx279133_flows_debugfs_init(struct zx279133_flow_offload *offload)
{
	return 0;
}
#endif

int zx279133_flow_offload_init(struct zx279133_eth *eth)
{
	struct zx279133_flow_offload *offload;
	int ret;

	offload = devm_kzalloc(eth->dev, sizeof(*offload), GFP_KERNEL);
	if (!offload)
		return -ENOMEM;
	offload->eth = eth;
	offload->ddr_used = devm_bitmap_zalloc(eth->dev, ZX279133_DDR_BUCKETS,
					       GFP_KERNEL);
	if (!offload->ddr_used)
		return -ENOMEM;
	mutex_init(&offload->lock);
	xa_init(&offload->flows);
	ret = rhashtable_init(&offload->key_flows,
			      &zx279133_flow_key_ht_params);
	if (ret)
		return ret;
	ret = rhashtable_init(&offload->reply_flows,
			      &zx279133_flow_reply_ht_params);
	if (ret) {
		rhashtable_destroy(&offload->key_flows);
		return ret;
	}
	INIT_LIST_HEAD(&offload->block_cb_list);
	INIT_LIST_HEAD(&offload->hosts);
	eth->flow_offload = offload;

	ret = devm_add_action_or_reset(eth->dev,
				       zx279133_flow_offload_cleanup, offload);
	if (ret)
		return ret;

	ret = devm_device_add_group(eth->dev, &zx279133_wan_ports_group);
	if (ret)
		return ret;

	return zx279133_flows_debugfs_init(offload);
}
