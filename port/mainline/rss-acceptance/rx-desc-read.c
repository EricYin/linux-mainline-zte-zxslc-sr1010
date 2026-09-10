// SPDX-License-Identifier: GPL-2.0-only
/* Read already-consumed IDM descriptor slots; never writes MMIO or RAM. */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	volatile uint32_t *idm, *ring;
	unsigned long args[3];
	uint32_t base;
	unsigned int i, j;
	unsigned int emitted = 0;
	int marked;
	int fd;
	char *end;

	if (argc != 4 && argc != 5)
		return 2;
	marked = argc == 5;
	if (marked && strcmp(argv[4], "marked"))
		return 2;
	for (i = 0; i < 3; i++) {
		errno = 0;
		args[i] = strtoul(argv[i + 1], &end, 0);
		if (errno || *end || end == argv[i + 1])
			return 2;
	}
	if (args[0] >= 16 || args[1] >= 2048 || !args[2] ||
	    args[2] > (marked ? 2048 : 64))
		return 2;
	fd = open("/dev/mem", O_RDONLY | O_SYNC);
	if (fd < 0)
		return 1;
	idm = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, 0x19280000);
	if (idm == MAP_FAILED) {
		close(fd);
		return 1;
	}
	base = idm[2];
	munmap((void *)idm, 4096);
	/* The configured coherent ring is page-aligned, within this board's RAM. */
	if ((base & 4095) || base < 0x80000000 || base > 0x9ff00000) {
		close(fd);
		return 1;
	}
	ring = mmap(NULL, 65536, PROT_READ, MAP_SHARED, fd, base + args[0] * 65536);
	if (ring == MAP_FAILED) {
		close(fd);
		return 1;
	}
	printf("{\"base\":%u,\"queue\":%lu,\"slots\":[", base, args[0]);
	for (i = 0; i < args[2]; i++) {
		unsigned int index = (args[1] + i) & 2047;

		if (marked && !(ring[index * 8 + 5] & (1U << 31)))
			continue;
		printf("%s{\"index\":%u,\"words\":[", emitted++ ? "," : "", index);
		for (j = 0; j < 8; j++)
			printf("%s%u", j ? "," : "", ring[index * 8 + j]);
		printf("]}");
	}
	puts("]}");
	munmap((void *)ring, 65536);
	close(fd);
	return 0;
}
