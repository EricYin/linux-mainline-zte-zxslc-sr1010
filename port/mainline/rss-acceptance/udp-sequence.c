// SPDX-License-Identifier: GPL-2.0-only
/* Bounded UDP byte, loss and per-flow order acceptance receiver. */
#include <arpa/inet.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#define FLOWS 8
#define COUNT 512
#define LENGTH 256

int main(int argc, char **argv)
{
	struct sockaddr_in local = { .sin_family = AF_INET,
				     .sin_port = htons(49000) };
	struct sockaddr_in6 local6 = { .sin6_family = AF_INET6,
				       .sin6_port = htons(49000) };
	struct sockaddr_storage peer;
	struct sockaddr *bind_address;
	socklen_t address_size;
	struct timeval timeout = { .tv_sec = 30 };
	unsigned char seen[FLOWS][COUNT] = { 0 }, packet[2048];
	unsigned int next[FLOWS] = { 0 }, per_flow[FLOWS] = { 0 };
	unsigned int received = 0, bad = 0, order = 0, duplicate = 0;
	unsigned int first, flows, count, expected, i, flow, sequence;
	int fd, size = 4 * 1024 * 1024;

	if (argc != 5)
		return 2;
	first = strtoul(argv[2], NULL, 10);
	flows = strtoul(argv[3], NULL, 10);
	count = strtoul(argv[4], NULL, 10);
	if (!flows || first + flows > FLOWS || !count || count > COUNT)
		return 2;
	if (strchr(argv[1], ':')) {
		if (inet_pton(AF_INET6, argv[1], &local6.sin6_addr) != 1)
			return 2;
		bind_address = (struct sockaddr *)&local6;
		address_size = sizeof(local6);
	} else {
		if (inet_pton(AF_INET, argv[1], &local.sin_addr) != 1)
			return 2;
		bind_address = (struct sockaddr *)&local;
		address_size = sizeof(local);
	}
	expected = flows * count;
	fd = socket(bind_address->sa_family, SOCK_DGRAM, 0);
	if (fd < 0 || setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &size, sizeof(size)) ||
	    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
	    bind(fd, bind_address, address_size)) {
		perror("UDP receiver setup");
		return 2;
	}
	printf("READY %s\n", argv[1]);
	fflush(stdout);
	while (received < expected) {
		socklen_t peer_length = sizeof(peer);
		uint32_t header[4];
		unsigned int peer_port;
		ssize_t length = recvfrom(fd, packet, sizeof(packet), 0,
					  (struct sockaddr *)&peer, &peer_length);

		if (length < 0) {
			if (errno == EINTR)
				continue;
			break;
		}
		if (!received) {
			timeout.tv_sec = 3;
			setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
		}
		if (length != LENGTH) {
			bad++;
			continue;
		}
		memcpy(header, packet, sizeof(header));
		peer_port = peer.ss_family == AF_INET6 ?
			ntohs(((struct sockaddr_in6 *)&peer)->sin6_port) :
			ntohs(((struct sockaddr_in *)&peer)->sin_port);
		flow = ntohl(header[1]);
		sequence = ntohl(header[2]);
		if (ntohl(header[0]) != 0x52535331 || flow < first ||
		    flow >= first + flows || sequence >= count ||
		    ntohl(header[3]) != count || peer_port != 48000 + flow) {
			bad++;
			continue;
		}
		for (i = sizeof(header); i < LENGTH; i++)
			if (packet[i] != (flow * 31 + sequence + i) % 251)
				bad++;
		if (seen[flow][sequence]) {
			duplicate++;
			continue;
		}
		seen[flow][sequence] = 1;
		if (sequence != next[flow])
			order++;
		next[flow] = sequence + 1;
		per_flow[flow]++;
		received++;
	}
	close(fd);
	printf("UDP_SEQUENCE received=%u expected=%u missing=%u bad=%u order=%u duplicate=%u result=%s\n",
	       received, expected, expected - received, bad, order, duplicate,
	       received == expected && !bad && !order && !duplicate ? "PASS" : "FAIL");
	for (i = first; i < first + flows; i++)
		printf("FLOW id=%u packets=%u\n", i, per_flow[i]);
	return received != expected || bad || order || duplicate;
}
