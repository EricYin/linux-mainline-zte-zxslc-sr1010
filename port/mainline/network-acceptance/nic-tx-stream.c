// SPDX-License-Identifier: GPL-2.0-only
/* Send a reproducible, nonzero TCP stream and require peer verification. */
#include <arpa/inet.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static int transfer(int fd, void *buffer, size_t length, int receive)
{
	char *p = buffer;

	while (length) {
		ssize_t n = receive ? recv(fd, p, length, 0) :
				      send(fd, p, length, MSG_NOSIGNAL);

		if (n < 0 && errno == EINTR)
			continue;
		if (n <= 0)
			return -1;
		p += n;
		length -= n;
	}
	return 0;
}

int main(int argc, char **argv)
{
	struct sockaddr_in peer = { .sin_family = AF_INET };
	struct timeval timeout = { .tv_sec = 15 };
	unsigned char buffer[8192];
	uint32_t header[2];
	unsigned int id, size, offset;
	char reply[2];
	int fd, result = EXIT_FAILURE;

	if (argc != 5) {
		fprintf(stderr, "usage: %s IPv4 PORT FLOW_ID BYTES\n", argv[0]);
		return EXIT_FAILURE;
	}
	peer.sin_port = htons(strtoul(argv[2], NULL, 0));
	if (inet_pton(AF_INET, argv[1], &peer.sin_addr) != 1)
		return EXIT_FAILURE;
	id = strtoul(argv[3], NULL, 0);
	size = strtoul(argv[4], NULL, 0);
	header[0] = htonl(id);
	header[1] = htonl(size);
	fd = socket(AF_INET, SOCK_STREAM, 0);
	if (fd < 0)
		return EXIT_FAILURE;
	setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
	setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
	if (connect(fd, (struct sockaddr *)&peer, sizeof(peer)) ||
	    transfer(fd, header, sizeof(header), 0))
		goto out;
	for (offset = 0; offset < size;) {
		unsigned int length = size - offset;
		unsigned int i;

		if (length > sizeof(buffer))
			length = sizeof(buffer);
		for (i = 0; i < length; i++)
			buffer[i] = (id * 31 + offset + i) % 251;
		if (transfer(fd, buffer, length, 0))
			goto out;
		offset += length;
	}
	shutdown(fd, SHUT_WR);
	if (!transfer(fd, reply, sizeof(reply), 1) && !memcmp(reply, "OK", 2))
		result = EXIT_SUCCESS;
out:
	printf("TCP_STREAM id=%u bytes=%u result=%s\n", id, size,
	       result == EXIT_SUCCESS ? "PASS" : "FAIL");
	close(fd);
	return result;
}
