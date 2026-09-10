// SPDX-License-Identifier: GPL-2.0-only
#include <linux/bpf.h>
#include <linux/if_link.h>
#include <net/if.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>

static volatile sig_atomic_t stopped;

#define INSN(op, dst, src, offset, value) \
	{ .code = (op), .dst_reg = (dst), .src_reg = (src), \
	  .off = (offset), .imm = (value) }

static void stop(int signal)
{
	(void)signal;
	stopped = 1;
}

int main(int argc, char **argv)
{
	struct rlimit limit = { RLIM_INFINITY, RLIM_INFINITY };
	struct bpf_insn insns[] = {
		{ .code = BPF_ALU64 | BPF_MOV | BPF_K, .dst_reg = BPF_REG_0,
		  .imm = XDP_PASS },
		{ .code = BPF_JMP | BPF_EXIT },
	};
	struct bpf_insn echo[] = {
		INSN(BPF_LDX | BPF_W | BPF_MEM, 2, 1, offsetof(struct xdp_md, data), 0),
		INSN(BPF_LDX | BPF_W | BPF_MEM, 3, 1, offsetof(struct xdp_md, data_end), 0),
		INSN(BPF_ALU64 | BPF_MOV | BPF_X, 4, 2, 0, 0),
		INSN(BPF_ALU64 | BPF_ADD | BPF_K, 4, 0, 0, 14),
		INSN(BPF_ALU64 | BPF_MOV | BPF_K, 0, 0, 0, XDP_PASS),
		INSN(BPF_JMP | BPF_JGT | BPF_X, 4, 3, 9, 0),
		INSN(BPF_LDX | BPF_W | BPF_MEM, 4, 2, 0, 0),
		INSN(BPF_LDX | BPF_H | BPF_MEM, 5, 2, 4, 0),
		INSN(BPF_LDX | BPF_W | BPF_MEM, 6, 2, 6, 0),
		INSN(BPF_LDX | BPF_H | BPF_MEM, 7, 2, 10, 0),
		INSN(BPF_STX | BPF_W | BPF_MEM, 2, 6, 0, 0),
		INSN(BPF_STX | BPF_H | BPF_MEM, 2, 7, 4, 0),
		INSN(BPF_STX | BPF_W | BPF_MEM, 2, 4, 6, 0),
		INSN(BPF_STX | BPF_H | BPF_MEM, 2, 5, 10, 0),
		INSN(BPF_ALU64 | BPF_MOV | BPF_K, 0, 0, 0, XDP_TX),
		INSN(BPF_JMP | BPF_EXIT, 0, 0, 0, 0),
	};
	struct bpf_insn queues[] = {
		INSN(BPF_LDX | BPF_W | BPF_MEM, 2, 1, offsetof(struct xdp_md, rx_queue_index), 0),
		INSN(BPF_JMP | BPF_JLE | BPF_K, 2, 0, 1, 1),
		INSN(BPF_ALU64 | BPF_MOV | BPF_K, 2, 0, 0, 2),
		INSN(BPF_STX | BPF_W | BPF_MEM, 10, 2, -4, 0),
		INSN(BPF_ALU64 | BPF_MOV | BPF_X, 2, 10, 0, 0),
		INSN(BPF_ALU64 | BPF_ADD | BPF_K, 2, 0, 0, -4),
		INSN(BPF_LD | BPF_DW | BPF_IMM, 1, BPF_PSEUDO_MAP_FD, 0, 0),
		INSN(0, 0, 0, 0, 0),
		INSN(BPF_JMP | BPF_CALL, 0, 0, 0, BPF_FUNC_map_lookup_elem),
		INSN(BPF_JMP | BPF_JEQ | BPF_K, 0, 0, 2, 0),
		INSN(BPF_ALU64 | BPF_MOV | BPF_K, 1, 0, 0, 1),
		INSN(BPF_STX | BPF_DW | BPF_XADD, 0, 1, 0, 0),
		INSN(BPF_ALU64 | BPF_MOV | BPF_K, 0, 0, 0, XDP_PASS),
		INSN(BPF_JMP | BPF_EXIT, 0, 0, 0, 0),
	};
	char verifier[4096] = { 0 };
	union bpf_attr attr = { 0 };
	unsigned int ifindex;
	int tx = argc == 3 && !strcmp(argv[2], "tx");
	int count_queues = argc == 3 && !strcmp(argv[2], "queues");
	int prog, link, map = -1;

	if ((argc != 2 && !tx && !count_queues) || !(ifindex = if_nametoindex(argv[1])))
		return 2;
	setrlimit(RLIMIT_MEMLOCK, &limit);
	if (count_queues) {
		attr.map_type = BPF_MAP_TYPE_ARRAY;
		attr.key_size = sizeof(uint32_t);
		attr.value_size = sizeof(uint64_t);
		attr.max_entries = 3;
		map = syscall(__NR_bpf, BPF_MAP_CREATE, &attr, sizeof(attr));
		if (map < 0) {
			perror("BPF_MAP_CREATE");
			return 1;
		}
		queues[6].imm = map;
		memset(&attr, 0, sizeof(attr));
	}
	attr.prog_type = BPF_PROG_TYPE_XDP;
	attr.expected_attach_type = BPF_XDP;
	attr.insn_cnt = sizeof(insns) / sizeof(insns[0]);
	attr.insns = (uintptr_t)insns;
	if (tx) {
		attr.insn_cnt = sizeof(echo) / sizeof(echo[0]);
		attr.insns = (uintptr_t)echo;
	}
	if (count_queues) {
		attr.insn_cnt = sizeof(queues) / sizeof(queues[0]);
		attr.insns = (uintptr_t)queues;
	}
	attr.license = (uintptr_t)"GPL";
	attr.log_buf = (uintptr_t)verifier;
	attr.log_size = sizeof(verifier);
	attr.log_level = 1;
	prog = syscall(__NR_bpf, BPF_PROG_LOAD, &attr, sizeof(attr));
	if (prog < 0) {
		perror("BPF_PROG_LOAD");
		fputs(verifier, stderr);
		return 1;
	}
	memset(&attr, 0, sizeof(attr));
	attr.link_create.prog_fd = prog;
	attr.link_create.target_ifindex = ifindex;
	attr.link_create.attach_type = BPF_XDP;
	attr.link_create.flags = XDP_FLAGS_DRV_MODE;
	link = syscall(__NR_bpf, BPF_LINK_CREATE, &attr, sizeof(attr));
	if (link < 0) {
		perror("BPF_LINK_CREATE");
		close(prog);
		return 1;
	}
	signal(SIGINT, stop);
	signal(SIGTERM, stop);
	puts(tx ? "XDP_TX_READY" : "XDP_PASS_READY");
	fflush(stdout);
	while (!stopped)
		sleep(1);
	close(link);
	close(prog);
	if (count_queues) {
		uint32_t key;
		uint64_t value;

		for (key = 0; key < 3; key++) {
			memset(&attr, 0, sizeof(attr));
			attr.map_fd = map;
			attr.key = (uintptr_t)&key;
			attr.value = (uintptr_t)&value;
			if (syscall(__NR_bpf, BPF_MAP_LOOKUP_ELEM, &attr, sizeof(attr)))
				return 1;
			printf("XDP_RX_QUEUE id=%u packets=%llu\n", key,
			       (unsigned long long)value);
		}
		close(map);
	}
	puts(tx ? "XDP_TX_DETACHED" : "XDP_PASS_DETACHED");
	return 0;
}
