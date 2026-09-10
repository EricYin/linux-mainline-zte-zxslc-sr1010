// SPDX-License-Identifier: GPL-2.0-only
/* Exercise the standard RSS ioctl with an explicit eight-bucket map. */
#include <errno.h>
#include <linux/ethtool.h>
#include <linux/sockios.h>
#include <net/if.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	struct ethtool_rxfh *rss;
	struct ifreq ifr = {};
	unsigned long mask;
	char *end;
	int fd, ret, error, i;
	pid_t child = -1;

	if ((argc != 3 && argc != 5) || strlen(argv[1]) >= IFNAMSIZ)
		return 2;
	errno = 0;
	mask = strtoul(argv[2], &end, 0);
	if (errno || *end || end == argv[2] || mask > 0xff)
		return 2;
	rss = calloc(1, sizeof(*rss) + 8 * sizeof(rss->rss_config[0]));
	if (!rss)
		return 1;
	rss->cmd = ETHTOOL_SRSSH;
	rss->indir_size = 8;
	for (i = 0; i < 8; i++)
		rss->rss_config[i] = (mask >> i) & 1;
	memcpy(ifr.ifr_name, argv[1], strlen(argv[1]) + 1);
	ifr.ifr_data = (void *)rss;
	fd = socket(AF_INET, SOCK_DGRAM, 0);
	if (fd < 0) {
		free(rss);
		return 1;
	}
	if (argc == 5) {
		child = fork();
		if (!child) {
			/* Board fixture: first read precedes the 2-second SerDes wait. */
			sleep(1);
			_exit(rename(argv[3], argv[4]) ? 1 : 0);
		}
		if (child < 0) {
			perror("fork");
			close(fd);
			free(rss);
			return 1;
		}
	}
	ret = ioctl(fd, SIOCETHTOOL, &ifr);
	error = ret ? errno : 0;
	printf("RSS_SET dev=%s mask=%02lx errno=%d result=%s\n",
	       argv[1], mask, error, ret ? "FAIL" : "PASS");
	if (child > 0) {
		int status;

		if (waitpid(child, &status, 0) != child || !WIFEXITED(status) ||
		    WEXITSTATUS(status)) {
			puts("RSS_FW_RESTORE result=FAIL");
			ret = -1;
		} else {
			puts("RSS_FW_RESTORE result=PASS");
		}
	}
	close(fd);
	free(rss);
	return !!ret;
}
