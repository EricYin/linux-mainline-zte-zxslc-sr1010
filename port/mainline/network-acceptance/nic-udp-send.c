// SPDX-License-Identifier: GPL-2.0-only
#include <arpa/inet.h>
#include <errno.h>
#include <net/if.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	struct sockaddr_storage addr = { 0 };
	unsigned char data[257];
	int family, fd, i;
	socklen_t addrlen;

	if (argc != 2) {
		fprintf(stderr, "usage: %s DESTINATION\n", argv[0]);
		return 2;
	}
	family = strchr(argv[1], ':') ? AF_INET6 : AF_INET;
	if (family == AF_INET6) {
		struct sockaddr_in6 *a = (void *)&addr;

		a->sin6_family = family;
		a->sin6_port = htons(49001);
		if (inet_pton(family, argv[1], &a->sin6_addr) != 1)
			return 2;
		if (IN6_IS_ADDR_LINKLOCAL(&a->sin6_addr))
			a->sin6_scope_id = if_nametoindex("eth0");
		addrlen = sizeof(*a);
	} else {
		struct sockaddr_in *a = (void *)&addr;

		a->sin_family = family;
		a->sin_port = htons(49001);
		if (inet_pton(family, argv[1], &a->sin_addr) != 1)
			return 2;
		addrlen = sizeof(*a);
	}
	fd = socket(family, SOCK_DGRAM, 0);
	if (fd < 0) {
		perror("socket");
		return 1;
	}
	for (i = 0; i < (int)sizeof(data); i++)
		data[i] = i;
	for (i = 0; i < 4; i++) {
		size_t len = 64 + i * 63;

		if (sendto(fd, data, len, 0, (void *)&addr, addrlen) != (ssize_t)len) {
			perror("sendto");
			close(fd);
			return 1;
		}
		printf("sent %zu bytes\n", len);
		usleep(200000);
	}
	close(fd);
	return 0;
}
