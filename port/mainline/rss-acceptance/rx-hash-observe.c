// SPDX-License-Identifier: GPL-2.0-only
/* Observe existing skb hash bits through six cBPF snapshot lengths. */
#include <arpa/inet.h>
#include <linux/filter.h>
#include <linux/if_packet.h>
#include <net/ethernet.h>
#include <net/if.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

static volatile sig_atomic_t stopped;

static void stop(int signal)
{
	(void)signal;
	stopped = 1;
}

int main(int argc, char **argv)
{
	struct sockaddr_ll address = { .sll_family = AF_PACKET,
				       .sll_protocol = htons(ETH_P_ALL) };
	struct pollfd sockets[6];
	unsigned int counts[6][64] = {}, i, j;
	unsigned char data[128];

	if (argc != 2)
		return 2;
	address.sll_ifindex = if_nametoindex(argv[1]);
	if (!address.sll_ifindex)
		return 2;
	for (i = 0; i < 6; i++) {
		struct sock_filter filter[] = {
			BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 12),
			BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, ETH_P_IPV6, 0, 11),
			BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 54),
			BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 48000, 0, 9),
			BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 56),
			BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 49000, 0, 7),
			BPF_STMT(BPF_LD | BPF_B | BPF_ABS, 20),
			BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, IPPROTO_UDP, 0, 5),
			BPF_STMT(BPF_LD | BPF_W | BPF_ABS, SKF_AD_OFF + SKF_AD_RXHASH),
			BPF_STMT(BPF_ALU | BPF_RSH | BPF_K, i * 6),
			BPF_STMT(BPF_ALU | BPF_AND | BPF_K, 63),
			BPF_STMT(BPF_ALU | BPF_ADD | BPF_K, 1),
			BPF_STMT(BPF_RET | BPF_A, 0),
			BPF_STMT(BPF_RET | BPF_K, 0),
		};
		struct sock_fprog program = { .len = 14, .filter = filter };

		sockets[i].fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
		sockets[i].events = POLLIN;
		if (sockets[i].fd < 0 || bind(sockets[i].fd, (void *)&address, sizeof(address)) ||
		    setsockopt(sockets[i].fd, SOL_SOCKET, SO_ATTACH_FILTER, &program, sizeof(program))) {
			perror("hash observer");
			return 1;
		}
	}
	signal(SIGTERM, stop);
	puts("HASH_OBSERVER_READY");
	fflush(stdout);
	while (!stopped) {
		if (poll(sockets, 6, 100) <= 0)
			continue;
		for (i = 0; i < 6; i++) {
			ssize_t length;

			if (!(sockets[i].revents & POLLIN))
				continue;
			length = recv(sockets[i].fd, data, sizeof(data), 0);
			if (length > 0 && length <= 64)
				counts[i][length - 1]++;
		}
	}
	for (i = 0; i < 6; i++) {
		close(sockets[i].fd);
		for (j = 0; j < 64; j++)
			if (counts[i][j])
				printf("HASH_PART part=%u value=%u count=%u\n", i, j, counts[i][j]);
	}
	return 0;
}
