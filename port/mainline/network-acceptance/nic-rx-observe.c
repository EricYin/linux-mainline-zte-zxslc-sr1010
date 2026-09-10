// SPDX-License-Identifier: GPL-2.0-only
#include <arpa/inet.h>
#include <errno.h>
#include <linux/if_ether.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stopped;

static void stop(int signal)
{
	(void)signal;
	stopped = 1;
}

static unsigned int be16(const unsigned char *p)
{
	return p[0] * 256U + p[1];
}

static double now(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(int argc, char **argv)
{
	struct sockaddr_ll bind_addr = { .sll_family = AF_PACKET };
	unsigned int received = 0, valid = 0;
	double deadline;
	int fd, enable = 1;

	if (argc != 3) {
		fprintf(stderr, "usage: %s INTERFACE SECONDS\n", argv[0]);
		return 2;
	}
	bind_addr.sll_ifindex = if_nametoindex(argv[1]);
	bind_addr.sll_protocol = htons(ETH_P_ALL);
	fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
	if (fd < 0 || !bind_addr.sll_ifindex ||
	    bind(fd, (void *)&bind_addr, sizeof(bind_addr)) ||
	    setsockopt(fd, SOL_PACKET, PACKET_AUXDATA, &enable, sizeof(enable))) {
		perror("packet socket");
		return 1;
	}
	setbuf(stdout, NULL);
	signal(SIGINT, stop);
	signal(SIGTERM, stop);
	printf("READY %s\n", argv[1]);
	deadline = now() + strtod(argv[2], NULL);
	while (!stopped && now() < deadline) {
		unsigned char packet[4096], control[CMSG_SPACE(sizeof(struct tpacket_auxdata))];
		struct sockaddr_ll from;
		struct iovec iov = { packet, sizeof(packet) };
		struct msghdr msg = { .msg_name = &from, .msg_namelen = sizeof(from),
			.msg_iov = &iov, .msg_iovlen = 1, .msg_control = control,
			.msg_controllen = sizeof(control) };
		struct pollfd pfd = { .fd = fd, .events = POLLIN };
		struct cmsghdr *cmsg;
		unsigned int type, offset = 14, version, proto, port, status = 0;
		ssize_t size;

		if (poll(&pfd, 1, 100) <= 0)
			continue;
		size = recvmsg(fd, &msg, MSG_DONTWAIT);
		if (size < 14 || from.sll_pkttype == PACKET_OUTGOING)
			continue;
		type = be16(packet + 12);
		while ((type == ETH_P_8021Q || type == ETH_P_8021AD) &&
		       offset + 4 <= (unsigned int)size) {
			type = be16(packet + offset + 2);
			offset += 4;
		}
		if (type == ETH_P_PPP_SES && offset + 8 <= (unsigned int)size) {
			type = be16(packet + offset + 6);
			type = type == 0x21 ? ETH_P_IP : type == 0x57 ? ETH_P_IPV6 : 0;
			offset += 8;
		}
		if (type == ETH_P_IP && offset + 20 <= (unsigned int)size) {
			version = 4;
			proto = packet[offset + 9];
			if (be16(packet + offset + 6) & 0x1fff)
				continue;
			offset += (packet[offset] & 15) * 4;
		} else if (type == ETH_P_IPV6 && offset + 40 <= (unsigned int)size) {
			version = 6;
			proto = packet[offset + 6];
			offset += 40;
			if ((proto == 0 || proto == 44) && offset + 8 <= (unsigned int)size) {
				if (proto == 44 && (be16(packet + offset + 2) & 0xfff8))
					continue;
				proto = packet[offset];
				offset += 8;
			}
		} else {
			continue;
		}
		if ((proto != 6 && proto != 17) || offset + 4 > (unsigned int)size)
			continue;
		port = be16(packet + offset);
		if (port < 48000 || port > 48999 || be16(packet + offset + 2) != 49000)
			continue;
		for (cmsg = CMSG_FIRSTHDR(&msg); cmsg; cmsg = CMSG_NXTHDR(&msg, cmsg))
			if (cmsg->cmsg_level == SOL_PACKET && cmsg->cmsg_type == PACKET_AUXDATA)
				status = ((struct tpacket_auxdata *)CMSG_DATA(cmsg))->tp_status;
		received++;
		valid += !!(status & TP_STATUS_CSUM_VALID);
		printf("RX ip=%u proto=%u port=%u bytes=%zd csum_valid=%u status=%#x\n",
		       version, proto, port, size, !!(status & TP_STATUS_CSUM_VALID), status);
	}
	printf("TOTAL received=%u csum_valid=%u\n", received, valid);
	close(fd);
	return 0;
}
