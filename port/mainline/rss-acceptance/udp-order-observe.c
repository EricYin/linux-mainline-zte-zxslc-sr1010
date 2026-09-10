// SPDX-License-Identifier: GPL-2.0-only
/* Observe iperf UDP sequence numbers without changing packet delivery. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <linux/filter.h>
#include <linux/if_packet.h>
#include <net/ethernet.h>
#include <net/if.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

static volatile sig_atomic_t stopping;

static void stop(int sig)
{
	(void)sig;
	stopping = 1;
}

int main(int argc, char **argv)
{
	struct sock_filter code[] = {
		BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 12),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, ETH_P_IP, 0, 9),
		BPF_STMT(BPF_LD | BPF_B | BPF_ABS, 14),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 0x45, 0, 7),
		BPF_STMT(BPF_LD | BPF_B | BPF_ABS, 23),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 17, 0, 5),
		BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 36),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 5203, 0, 3),
		BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 38),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 26, 0, 1),
		BPF_STMT(BPF_RET | BPF_K, 64),
		BPF_STMT(BPF_RET | BPF_K, 0),
	};
	struct sock_fprog filter = { .len = sizeof(code) / sizeof(code[0]), .filter = code };
	struct sockaddr_ll local = { .sll_family = AF_PACKET, .sll_protocol = htons(ETH_P_IP) };
	struct timeval timeout = { .tv_usec = 200000 };
	struct tpacket_stats stats;
	struct mmsghdr messages[32] = { 0 };
	struct iovec vectors[32];
	unsigned char packets[32][64];
	uint32_t highest[8] = { 0 };
	unsigned long counts[8] = { 0 }, backward = 0, same = 0, gaps = 0;
	unsigned int base, events = 0, i;
	socklen_t stats_size = sizeof(stats);
	int fd, size = 4 * 1024 * 1024;

	if (argc != 3)
		return 2;
	base = strtoul(argv[2], NULL, 10);
	local.sll_ifindex = if_nametoindex(argv[1]);
	if (!local.sll_ifindex || !base || base > 65528)
		return 2;
	fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_IP));
	if (fd < 0 || setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &size, sizeof(size)) ||
	    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
	    setsockopt(fd, SOL_SOCKET, SO_ATTACH_FILTER, &filter, sizeof(filter)) ||
	    bind(fd, (struct sockaddr *)&local, sizeof(local))) {
		perror("observer setup");
		return 2;
	}
	for (i = 0; i < 32; i++) {
		vectors[i].iov_base = packets[i];
		vectors[i].iov_len = sizeof(packets[i]);
		messages[i].msg_hdr.msg_iov = &vectors[i];
		messages[i].msg_hdr.msg_iovlen = 1;
	}
	signal(SIGTERM, stop);
	signal(SIGINT, stop);
	puts("UDP_ORDER_READY");
	fflush(stdout);
	while (!stopping) {
		int n = recvmmsg(fd, messages, 32, MSG_WAITFORONE, NULL);

		if (n < 0) {
			if (errno == EINTR || errno == EAGAIN)
				continue;
			perror("recvmmsg");
			return 1;
		}
		for (int j = 0; j < n; j++) {
			uint32_t sequence;
			uint16_t port;
			unsigned int flow;

			if (messages[j].msg_len < 54)
				continue;
			memcpy(&port, packets[j] + 34, sizeof(port));
			if (ntohs(port) < base || ntohs(port) >= base + 8)
				continue;
			flow = ntohs(port) - base;
			memcpy(&sequence, packets[j] + 50, sizeof(sequence));
			sequence = ntohl(sequence);
			if (counts[flow] && sequence <= highest[flow]) {
				backward += sequence < highest[flow];
				same += sequence == highest[flow];
				if (events++ < 64)
					printf("ORDER_EVENT port=%u sequence=%u previous=%u\n",
					       ntohs(port), sequence, highest[flow]);
			}
			if (counts[flow] && sequence > highest[flow] + 1)
				gaps += sequence - highest[flow] - 1;
			if (sequence > highest[flow])
				highest[flow] = sequence;
			counts[flow]++;
		}
	}
	if (getsockopt(fd, SOL_PACKET, PACKET_STATISTICS, &stats, &stats_size))
		return 1;
	printf("UDP_ORDER backward=%lu same=%lu gaps=%lu capture_packets=%u capture_drops=%u\n",
	       backward, same, gaps, stats.tp_packets, stats.tp_drops);
	for (i = 0; i < 8; i++)
		printf("ORDER_FLOW port=%u packets=%lu highest=%u\n", base + i, counts[i], highest[i]);
	close(fd);
	return 0;
}
