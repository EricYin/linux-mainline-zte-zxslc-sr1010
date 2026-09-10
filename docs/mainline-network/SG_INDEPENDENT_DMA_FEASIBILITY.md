# Independent DMA aggregation feasibility

## Verdict

**Not currently implementable with an identified controller/channel.** The
architecture of DMA-copying skb fragments into a contiguous staging buffer,
then submitting ordinary IDM TX, is viable in principle. The available
ZX279133 evidence does not yet identify an independent memory-to-memory DMA
engine with a usable register map, completion mechanism and driver.

This does not prove that the silicon lacks a general DMA block. A concrete
clock-name clue exists, but a controller bring-up prerequisite remains.
No transfer test or performance claim is justified without that prerequisite.

## Evidence checked

| Source | Result |
| --- | --- |
| Factory `zx279133-sr1010.dts` | `top_clk_gate0` names `dma_aclk` and `dma_pclk` at `0x10e10030`, bits 4/5. No `dma-controller` node, DMA channel specification or matching controller register resource was found. |
| Current mainline ZTE DTS/DTSI and port DTS/DTSI | No `dma-controller`, `dma-channels`, PL330 or DW AXI DMAC description found. |
| `out/kernel/.config` | `# CONFIG_DMADEVICES is not set`. Merely changing this option would not supply a controller resource or provider. |
| Live normal mainline RAM boot | `/sys/class/dma` absent; AMBA device inventory contains the serial controller; no named DMA controller in platform-device, interrupt or iomem searches. This establishes no currently exposed channel, not hardware absence. |
| Archived factory iomem/interrupts | No matching named general DMA resource or interrupt. |
| Archived factory kallsyms | No identified PL330/PL08x/DW DMA provider or general memcpy/channel implementation. Generic DMA helper/stub symbols do not establish a provider. |
| All 28 distinct saved vendor module binaries | Symbol scan found no DMAEngine registration, DMA memcpy/channel implementation or the checked standard controller names. |
| `bspdriver.ko` | Recovered functions implement I2C/board support; no DMA controller path identified. |
| U-Boot `uboot_proper.elf.i64` | No named DMA/memcpy/controller function candidate. String hits for DMAC refer to MAC-address handling. The binary's incomplete naming limits the strength of this negative result. |
| Factory `Image` | No embedded IKCONFIG markers found; a factory build configuration was not inferred. |

The clock definitions are at factory DTS lines 209..218. They establish
named gate outputs, not a peripheral base address, controller identity,
channel count, IRQ, reset state or support for memory-to-memory operation.
No unknown MMIO range was probed or DMA/reset/clock register written.

## Similar names that do not solve this requirement

- `dmac_flush_range`, `dmac_inv_range` and related AArch64 symbols are cache
  maintenance interfaces, not independently executing memory-copy channels.
- `register_xedmac_int` is the already recovered PON MAC interrupt hook;
  `DMAC` also appears in destination-MAC matching functions. These are not
  evidence of a general DMA controller.
- The ordinary IDM/WOE engines are the specialized packet paths already
  investigated. DMA capability of a network peripheral does not expose a
  general source→destination memory-copy transaction.
- `ipsec.ko` has cryptographic DMA-related datapaths, but its recovered
  `ipsec_no_crypt_auth` software path does not establish a hardware memcpy
  service. No independently usable passthrough-copy contract was recovered.

A public web search did not provide an authoritative ZX279133 DMA register
map or controller driver. Unrelated-device examples and third-party boot logs
were not used to identify or program a controller.

## What would make aggregation possible

The missing first gate is a controller identification: a matching vendor
driver or SoC register map specifying source/destination addresses, transfer
lengths, address increment behavior, channel/reset/IRQ control, completion
and termination semantics. Then its memory-to-memory capability must be
verified on this board. Linux calls this capability `DMA_MEMCPY`; a controller
driver must actually implement/register the supported operations.
[Linux DMAEngine provider documentation](https://docs.kernel.org/driver-api/dmaengine/provider.html)

Once such a provider exists, the network integration would have two separate
ownership phases:

1. Keep skb source pages alive, map them for the copy device, and copy each
   fragment into its offset in a contiguous staging buffer. Wait for all
   relevant copy operations to complete before handing the destination to
   IDM. Preserve network packet ordering and handle copy errors/termination.
2. Map/synchronize the staging buffer for IDM as required, submit through
   ordinary TX, and retain that buffer until ordinary IDM TX completion.

A DMA address obtained for one device cannot simply be assumed valid for
the other device's DMA domain. Hardware copying also does not eliminate the
memory traffic: it replaces the CPU copy with DMA reads/writes, followed by
IDM's read of the resulting frame.

Correctness acceptance would start with standalone copies over varying
sizes/offsets and completion/error behavior, before network integration.
`dmatest` is a standard starting point once an appropriate channel exists.
[Linux DMA test guide](https://docs.kernel.org/driver-api/dmaengine/dmatest.html)

Performance must then be measured against current CPU linearization, with
identical packets and fragment layouts. Channel submission, multiple copy
descriptors, synchronization and staging-buffer management can outweigh the
CPU-copy saving, especially for small packets. No threshold or speedup is
claimed by this audit.

## Practical decision and scope

Keep the existing linearized ordinary TX path as the working baseline.
Independent DMA aggregation is a **conditional alternative awaiting controller
identification**, not an available second implementation route today. The
next material needed is a ZX279133 DMA register specification or matching
controller driver; the two clock names alone do not justify guessing a
PL330/DW controller or adding a fabricated DTS node.

This task performed static analysis and read-only runtime inventory. No
production/probe code or kernel configuration was changed, no DMA channel
was programmed, no module was loaded, and no new transfer/network acceptance
test was run. It does not enable or validate SG.
