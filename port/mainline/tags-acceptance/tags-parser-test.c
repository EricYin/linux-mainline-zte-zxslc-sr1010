// SPDX-License-Identifier: GPL-2.0-only
/* RAM-only tests of the production parser; no MTD accesses. */
#include <linux/module.h>

#include "zte-tags.h"

static void finish_table(u8 *b, size_t size)
{
	put_unaligned_le32(0x33333333, b);
	put_unaligned_le32(size - 12, b + 4);
	put_unaligned_le32(crc32_le(~0U, b + 12, size - 12) ^ ~0U, b + 8);
}

static void make_table(u8 *b)
{
	static const u8 mac[ETH_ALEN] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x55 };

	memset(b, 0, 80);
	put_unaligned_le32(2, b + 12);
	put_unaligned_le32(6, b + 16);
	put_unaligned_le16(0x0100, b + 28);
	memcpy(b + 30, mac, ETH_ALEN);
	finish_table(b, 36);
}

static int __init tags_parser_test_init(void)
{
	u8 storage[81], *b = storage + 1;
	int test, expected, actual;
	u16 tag;
	size_t size;

	for (test = 0; test < 15; test++) {
		make_table(b);
		tag = 0x0100;
		size = 36;
		expected = -EINVAL;
		switch (test) {
		case 0: /* Valid, deliberately unaligned buffer. */
			expected = 30;
			break;
		case 1:
			b[0] = 0;
			break;
		case 2:
			b[8] ^= 1;
			expected = -EBADMSG;
			break;
		case 3:
			size = 11;
			break;
		case 4:
			put_unaligned_le32(~0U, b + 4);
			break;
		case 5:
			put_unaligned_le32(~0U, b + 12);
			finish_table(b, size);
			break;
		case 6:
			put_unaligned_le32(~0U, b + 16);
			finish_table(b, size);
			break;
		case 7: /* CRC-valid truncated entry after a valid MAC. */
			size++;
			finish_table(b, size);
			break;
		case 8: /* Duplicate base MAC keys are ambiguous. */
			memcpy(b + 36, b + 12, 24);
			size = 60;
			finish_table(b, size);
			break;
		case 9:
			b[30] |= 1;
			finish_table(b, size);
			break;
		case 10:
			put_unaligned_le16(0x0101, b + 28);
			finish_table(b, size);
			expected = -ENOENT;
			break;
		case 11: /* Unknown odd-sized entry moves the MAC value. */
			memmove(b + 31, b + 12, 24);
			memset(b + 12, 0, 19);
			put_unaligned_le32(1, b + 12);
			put_unaligned_le32(2, b + 16);
			b[28] = 'X';
			size = 55;
			finish_table(b, size);
			expected = 49;
			break;
		case 12: /* Select the second tag, not the first MAC record. */
		case 13: /* Reject a malformed second MAC. */
			memcpy(b + 36, b + 12, 24);
			put_unaligned_le16(0x0101, b + 52);
			b[59]++;
			if (test == 13)
				b[54] |= 1;
			size = 60;
			finish_table(b, size);
			tag = 0x0101;
			expected = test == 12 ? 54 : -EINVAL;
			break;
		case 14:
			tag = 0x0101;
			expected = -ENOENT;
			break;
		}
		actual = zte_tags_mac_offset(b, size, tag);
		if (actual != expected) {
			pr_err("tags-parser-test: case %d got %d expected %d\n",
			       test, actual, expected);
			return -EINVAL;
		}
	}
	pr_info("tags-parser-test: PASS 15/15\n");
	return 0;
}

static void __exit tags_parser_test_exit(void)
{
}

module_init(tags_parser_test_init);
module_exit(tags_parser_test_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("RAM-only ZTE tags parser acceptance tests");
