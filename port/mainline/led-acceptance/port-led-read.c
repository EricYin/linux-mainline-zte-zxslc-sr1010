// SPDX-License-Identifier: GPL-2.0-only
/* LED inventory and reversible mode test, serialized with the driver. */
#include <linux/delay.h>
#include <linux/mdio.h>
#include <linux/module.h>
#include <linux/phy.h>

static bool link_activity;
module_param(link_activity, bool, 0400);
MODULE_PARM_DESC(link_activity, "Temporarily select all-speed link/activity; restore on unload");

static int single_output = -1;
module_param(single_output, int, 0400);
MODULE_PARM_DESC(single_output, "Select one group-0 LED output (0..3), or -1 for all");

static bool reference_mode;
module_param(reference_mode, bool, 0400);
MODULE_PARM_DESC(reference_mode, "Test the RTLPlayground global LED mode; restore on unload");

static struct mii_bus *test_bus;
static const u16 mode_regs[] = { 0x6544, 0x6548, 0x6520 };
static u32 saved_modes[ARRAY_SIZE(mode_regs)];
static unsigned int saved_count;

static int smi_wait(struct mii_bus *bus)
{
	int i, val;

	for (i = 0; i < 100; i++) {
		val = __mdiobus_read(bus, 29, 0x15);
		if (val < 0)
			return val;
		if (!(val & 4))
			return 0;
		usleep_range(10, 20);
	}
	return -ETIMEDOUT;
}

static int read_switch(struct mii_bus *bus, u16 reg, u32 *value)
{
	int ret, lo, hi;

	ret = smi_wait(bus);
	if (!ret)
		ret = __mdiobus_write(bus, 29, 0x16, reg);
	if (!ret)
		ret = __mdiobus_write(bus, 29, 0x15, 0x1b);
	if (!ret)
		ret = smi_wait(bus);
	if (ret)
		return ret;
	lo = __mdiobus_read(bus, 29, 0x17);
	if (lo < 0)
		return lo;
	hi = __mdiobus_read(bus, 29, 0x18);
	if (hi < 0)
		return hi;
	*value = lo | (u32)hi << 16;
	pr_info("port-led switch %04x=%08x\n", reg, *value);
	return 0;
}

static int write_switch(struct mii_bus *bus, u16 reg, u32 value)
{
	int ret = smi_wait(bus);

	if (!ret)
		ret = __mdiobus_write(bus, 29, 0x16, reg);
	if (!ret)
		ret = __mdiobus_write(bus, 29, 0x17, value & 0xffff);
	if (!ret)
		ret = __mdiobus_write(bus, 29, 0x18, value >> 16);
	if (!ret)
		ret = __mdiobus_write(bus, 29, 0x15, 0x19);
	if (!ret)
		ret = smi_wait(bus);
	return ret;
}

static void restore_modes(struct mii_bus *bus)
{
	while (saved_count) {
		unsigned int i = --saved_count;
		int ret = write_switch(bus, mode_regs[i], saved_modes[i]);

		pr_info("port-led restore %04x=%08x status=%d\n",
			mode_regs[i], saved_modes[i], ret);
	}
}

static int __init port_led_read_init(void)
{
	struct mii_bus *bus;
	unsigned int reg;
	u32 value;
	int ret = 0;

	if (single_output < -1 || single_output > 3)
		return -EINVAL;
	if (reference_mode && !link_activity)
		return -EINVAL;
	bus = mdio_find_bus("14f01000.mdio");
	if (!bus)
		return -ENODEV;
	mutex_lock(&bus->mdio_lock);
	for (reg = 0x6520; reg <= 0x657c && !ret; reg += 4)
		ret = read_switch(bus, reg, &value);
	for (reg = 0x65d8; reg <= 0x65f4 && !ret; reg += 4)
		ret = read_switch(bus, reg, &value);
	for (reg = 0x7f8c; reg <= 0x7f94 && !ret; reg += 4)
		ret = read_switch(bus, reg, &value);
	if (link_activity && !ret) {
		for (reg = 0; reg < 2 && !ret; reg++) {
			u32 mode = 0x01750175;
			unsigned int output = reg ? 0 : 2;

			if (single_output >= 0) {
				mode = 0;
				if (single_output == output)
					mode = 0x0175;
				else if (single_output == output + 1)
					mode = 0x01750000;
			}
			ret = read_switch(bus, 0x6544 + reg * 4,
					  &saved_modes[reg]);
			if (ret)
				break;
			saved_count++;
			/* 10/100/1000/2500 link and activity, both outputs. */
			ret = write_switch(bus, 0x6544 + reg * 4, mode);
			if (!ret)
				ret = read_switch(bus, 0x6544 + reg * 4, &value);
			if (!ret && value != mode)
				ret = -EIO;
		}
		if (!ret && reference_mode) {
			ret = read_switch(bus, mode_regs[2], &saved_modes[2]);
			if (!ret) {
				saved_count++;
				ret = write_switch(bus, mode_regs[2], 0x0021e6b0);
			}
			if (!ret)
				ret = read_switch(bus, mode_regs[2], &value);
			if (!ret && value != 0x0021e6b0)
				ret = -EIO;
		}
		if (ret)
			restore_modes(bus);
	}
	mutex_unlock(&bus->mdio_lock);
	if (!ret && saved_count)
		test_bus = bus;
	else
		put_device(&bus->dev);
	return ret;
}

static void __exit port_led_read_exit(void)
{
	if (test_bus) {
		mutex_lock(&test_bus->mdio_lock);
		restore_modes(test_bus);
		mutex_unlock(&test_bus->mdio_lock);
		put_device(&test_bus->dev);
	}
}

module_init(port_led_read_init);
module_exit(port_led_read_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("SR1010 port LED register inventory and reversible mode test");
