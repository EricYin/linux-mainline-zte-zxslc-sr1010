// SPDX-License-Identifier: GPL-2.0-only
/* Read the WAN PHY without changing its configuration. */
#include <linux/mdio.h>
#include <linux/module.h>
#include <linux/phy.h>

#define ZX279051_INIT_C22 0
#define ZX279051_INIT_C45 1

struct zx279051_init_write {
	u8 access;
	u8 devad;
	u16 reg;
	u16 val;
};

#include "../../../linux-6.18.38/drivers/net/phy/zx279051-fullmask.h"

static int show_reg(struct mii_bus *bus, int devad, u16 reg)
{
	int val;

	if (devad < 0)
		val = __mdiobus_read(bus, 1, reg);
	else
		val = __mdiobus_c45_read(bus, 1, devad, reg);
	if (val < 0)
		return val;
	pr_info("wan-led %02x.%04x=%04x\n", devad & 0xff, reg, val);
	return 0;
}

static int __init wan_led_read_init(void)
{
	struct mii_bus *bus;
	unsigned int i;
	int ret = 0;

	bus = mdio_find_bus("14f02000.mdio");
	if (!bus)
		return -ENODEV;
	mutex_lock(&bus->mdio_lock);
	for (i = 0; i < 32 && !ret; i++)
		ret = show_reg(bus, -1, i);
	for (i = 0; i < 64 && !ret; i++)
		ret = show_reg(bus, 31, i);
	for (i = 0; i < ARRAY_SIZE(zx279051_fullmask_writes) && !ret; i++) {
		const struct zx279051_init_write *w = &zx279051_fullmask_writes[i];

		if (w->access == ZX279051_INIT_C45 &&
		    (w->devad != 31 || w->reg >= 64))
			ret = show_reg(bus, w->devad, w->reg);
	}
	mutex_unlock(&bus->mdio_lock);
	put_device(&bus->dev);
	return ret;
}

static void __exit wan_led_read_exit(void)
{
}

module_init(wan_led_read_init);
module_exit(wan_led_read_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Read-only ZX279051 initialization register inventory");
