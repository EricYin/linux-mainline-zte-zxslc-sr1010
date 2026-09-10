// SPDX-License-Identifier: GPL-2.0-only
/* Board-side byte verifier for simultaneous WAN/LAN TCP receive tests. */
#include <arpa/inet.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>

static int verify(int fd)
{
	struct timeval timeout = { .tv_sec = 15 };
	unsigned char data[65536];
	uint32_t header[2];
	uint64_t count = 0, bad = 0;
	unsigned int id, length;
	size_t offset = 0;
	ssize_t n;
	int valid;

	setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
	while (offset < sizeof(header)) {
		n = recv(fd, (char *)header + offset, sizeof(header) - offset, 0);
		if (n <= 0)
			return 1;
		offset += n;
	}
	id = ntohl(header[0]);
	length = ntohl(header[1]);
	while ((n = recv(fd, data, sizeof(data), 0)) > 0) {
		unsigned int phase = (id * 31ULL + count) % 251;
		ssize_t i;

		for (i = 0; i < n; i++) {
			bad += data[i] != phase;
			if (++phase == 251)
				phase = 0;
		}
		count += n;
	}
	valid = n == 0 && length && count == length && !bad;
	if (send(fd, valid ? "OK" : "NO", 2, MSG_NOSIGNAL) != 2)
		valid = 0;
	printf("RX_STREAM id=%u bytes=%llu bad=%llu result=%s\n", id,
	       (unsigned long long)count, (unsigned long long)bad,
	       valid ? "PASS" : "FAIL");
	fflush(stdout);
	return !valid;
}

int main(int argc, char **argv)
{
	struct sockaddr_in address = { .sin_family = AF_INET };
	struct timeval timeout = { .tv_sec = 15 };
	pid_t children[8];
	unsigned int i, started = 0;
	int listener, fd, status, failed = 0, one = 1;

	if (argc != 3 || inet_pton(AF_INET, argv[1], &address.sin_addr) != 1)
		return 2;
	address.sin_port = htons(strtoul(argv[2], NULL, 10));
	listener = socket(AF_INET, SOCK_STREAM, 0);
	if (listener < 0)
		return 1;
	setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
	setsockopt(listener, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
	if (bind(listener, (struct sockaddr *)&address, sizeof(address)) ||
	    listen(listener, 8)) {
		perror("listen");
		close(listener);
		return 1;
	}
	printf("READY %s\n", argv[1]);
	fflush(stdout);
	for (i = 0; i < 8; i++) {
		fd = accept(listener, NULL, NULL);
		if (fd < 0) {
			failed = 1;
			break;
		}
		children[started] = fork();
		if (!children[started]) {
			close(listener);
			status = verify(fd);
			close(fd);
			return status;
		}
		close(fd);
		if (children[started] < 0) {
			failed = 1;
			break;
		}
		started++;
	}
	close(listener);
	for (i = 0; i < started; i++) {
		while (waitpid(children[i], &status, 0) < 0) {
			if (errno != EINTR)
				return 1;
		}
		failed |= !WIFEXITED(status) || WEXITSTATUS(status);
	}
	return !!failed;
}
