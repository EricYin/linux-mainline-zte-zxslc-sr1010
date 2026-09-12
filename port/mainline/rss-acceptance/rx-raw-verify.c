// SPDX-License-Identifier: GPL-2.0-only
/* Count and verify exact IPv4/IPv6 fixtures delivered to a netdev. */
#include <arpa/inet.h>
#include <errno.h>
#include <linux/filter.h>
#include <linux/if_packet.h>
#include <net/ethernet.h>
#include <net/if.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
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
	unsigned char expected[2048], frame[2048];
	struct sockaddr_ll address = { .sll_family = AF_PACKET };
	struct tpacket_stats stats = {};
	struct pollfd pollfd;
	socklen_t size = sizeof(stats);
	unsigned int length, i, received = 0, bad = 0, value, transport;
	unsigned int address_offset, address_length, protocol_offset;
	unsigned int target = 0;
	int fd;
	int receive_buffer = 4 * 1024 * 1024;

	if ((argc != 3 && argc != 4 && argc != 5) || strlen(argv[2]) % 2 || strlen(argv[2]) > 4096)
		return 2;
	length = strlen(argv[2]) / 2;
	for (i = 0; i < length; i++) {
		if (sscanf(argv[2] + 2 * i, "%2x", &value) != 1)
			return 2;
		expected[i] = value;
	}
	if (length < 38)
		return 2;
	if (expected[12] == 8 && expected[13] == 0 &&
	    expected[14] >> 4 == 4 && (expected[14] & 15) >= 5) {
		transport = 14 + (expected[14] & 15) * 4;
		address_offset = 26;
		address_length = 8;
		protocol_offset = 23;
	} else if (expected[12] == 0x86 && expected[13] == 0xdd &&
		   expected[14] >> 4 == 6) {
		transport = 54;
		address_offset = 22;
		address_length = 32;
		protocol_offset = 20;
	} else {
		return 2;
	}
	if (length < transport + 4)
		return 2;
	address.sll_ifindex = if_nametoindex(argv[1]);
	address.sll_protocol = htons(ETH_P_ALL);
	if (!address.sll_ifindex)
		return 2;
	fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
	if (fd < 0 || bind(fd, (void *)&address, sizeof(address))) {
		perror("packet socket");
		return 1;
	}
	if (setsockopt(fd, SOL_SOCKET, SO_RCVBUFFORCE, &receive_buffer,
		       sizeof(receive_buffer))) {
		perror("receive buffer");
		close(fd);
		return 1;
	}
	if (argc == 5)
		target = strtoul(argv[4], NULL, 10);
	if (argc >= 4 && strcmp(argv[3], "-")) {
		char *end;
		unsigned long hash = strtoul(argv[3], &end, 16);
		struct sock_filter filter[] = {
			BPF_STMT(BPF_LD | BPF_W | BPF_ABS, SKF_AD_OFF + SKF_AD_RXHASH),
			BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, hash, 0, 1),
			BPF_STMT(BPF_RET | BPF_K, 0xffffffff),
			BPF_STMT(BPF_RET | BPF_K, 0),
		};
		struct sock_fprog program = { .len = 4, .filter = filter };

		/* SKF_AD_RXHASH reads skb->hash without generating a software hash. */
		if (*end || hash > 0xffffffffUL ||
		    setsockopt(fd, SOL_SOCKET, SO_ATTACH_FILTER, &program, sizeof(program))) {
			perror("hash filter");
			close(fd);
			return 1;
		}
	}
	signal(SIGTERM, stop);
	signal(SIGINT, stop);
	pollfd.fd = fd;
	pollfd.events = POLLIN;
	puts("RAW_VERIFY_READY");
	fflush(stdout);
	while (!stopped && (!target || received < target)) {
		struct sockaddr_ll from;
		socklen_t from_size = sizeof(from);
		ssize_t bytes;

		if (poll(&pollfd, 1, 100) <= 0)
			continue;
		bytes = recvfrom(fd, frame, sizeof(frame), 0, (void *)&from, &from_size);
		if (bytes < transport + 4 || from.sll_pkttype == PACKET_OUTGOING ||
		    memcmp(frame + 12, expected + 12, 2) ||
		    frame[protocol_offset] != expected[protocol_offset] ||
		    frame[14] != expected[14] ||
		    memcmp(frame + address_offset, expected + address_offset, address_length) ||
		    memcmp(frame + transport, expected + transport, 4))
			continue;
		received++;
		if (bytes != length || memcmp(frame, expected, length))
			bad++;
	}
	if (getsockopt(fd, SOL_PACKET, PACKET_STATISTICS, &stats, &size)) {
		perror("packet statistics");
		close(fd);
		return 1;
	}
	printf("RAW_VERIFY received=%u bad=%u socket_drops=%u\n",
	       received, bad, stats.tp_drops);
	close(fd);
	return !received || bad || stats.tp_drops;
}
