// SPDX-License-Identifier: GPL-2.0-only
/* Drop only benchmark UDP data after DMA; keep iperf control/handshake traffic. */
#include <arpa/inet.h>
#include <linux/bpf.h>
#include <linux/if_link.h>
#include <net/if.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>

#define INSN(op, dst, src, offset, value) \
	{ .code = (op), .dst_reg = (dst), .src_reg = (src), .off = (offset), .imm = (value) }

static volatile sig_atomic_t stopped;

static void stop(int signal)
{
	(void)signal;
	stopped = 1;
}

int main(int argc, char **argv)
{
	struct rlimit limit = { RLIM_INFINITY, RLIM_INFINITY };
	struct bpf_insn code[] = {
		INSN(BPF_LDX | BPF_W | BPF_MEM, 2, 1, offsetof(struct xdp_md, data), 0),
		INSN(BPF_LDX | BPF_W | BPF_MEM, 3, 1, offsetof(struct xdp_md, data_end), 0),
		INSN(BPF_ALU64 | BPF_MOV | BPF_X, 4, 2, 0, 0),
		INSN(BPF_ALU64 | BPF_ADD | BPF_K, 4, 0, 0, 42),
		INSN(BPF_ALU64 | BPF_MOV | BPF_K, 0, 0, 0, XDP_PASS),
		INSN(BPF_JMP | BPF_JGT | BPF_X, 4, 3, 14, 0),
		INSN(BPF_LDX | BPF_H | BPF_MEM, 4, 2, 12, 0),
		INSN(BPF_JMP | BPF_JNE | BPF_K, 4, 0, 12, htons(0x0800)),
		INSN(BPF_LDX | BPF_B | BPF_MEM, 4, 2, 14, 0),
		INSN(BPF_JMP | BPF_JNE | BPF_K, 4, 0, 10, 0x45),
		INSN(BPF_LDX | BPF_B | BPF_MEM, 4, 2, 23, 0),
		INSN(BPF_JMP | BPF_JNE | BPF_K, 4, 0, 8, 17),
		INSN(BPF_LDX | BPF_H | BPF_MEM, 4, 2, 20, 0),
		INSN(BPF_ALU64 | BPF_AND | BPF_K, 4, 0, 0, htons(0x3fff)),
		INSN(BPF_JMP | BPF_JNE | BPF_K, 4, 0, 5, 0),
		INSN(BPF_LDX | BPF_H | BPF_MEM, 4, 2, 36, 0),
		INSN(BPF_JMP | BPF_JNE | BPF_K, 4, 0, 3, htons(5203)),
		INSN(BPF_LDX | BPF_H | BPF_MEM, 4, 2, 38, 0),
		INSN(BPF_JMP | BPF_JNE | BPF_K, 4, 0, 1, 0),
		INSN(BPF_ALU64 | BPF_MOV | BPF_K, 0, 0, 0, XDP_DROP),
		INSN(BPF_JMP | BPF_EXIT, 0, 0, 0, 0),
	};
	char verifier[4096] = {}, *end;
	union bpf_attr attr = {};
	unsigned long payload;
	unsigned int ifindex;
	int prog, link;

	if (argc != 3 || !(ifindex = if_nametoindex(argv[1])))
		return 2;
	payload = strtoul(argv[2], &end, 10);
	if (*end || payload < 16 || payload > 1472)
		return 2;
	code[18].imm = htons(payload + 8);
	setrlimit(RLIMIT_MEMLOCK, &limit);
	attr.prog_type = BPF_PROG_TYPE_XDP;
	attr.expected_attach_type = BPF_XDP;
	attr.insn_cnt = sizeof(code) / sizeof(code[0]);
	attr.insns = (uintptr_t)code;
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
	attr = (union bpf_attr){};
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
	puts("XDP_PPS_SINK_READY");
	fflush(stdout);
	while (!stopped)
		sleep(1);
	close(link);
	close(prog);
	puts("XDP_PPS_SINK_DETACHED");
	return 0;
}
