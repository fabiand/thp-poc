
> Human touch: This is AI slop. I need a skill to make AI write more human like.

# THP Memory Test Suite

This repository contains tools to verify and test the memory lifecycle used by hypervisors (like QEMU) and orchestrators (like KubeVirt) when using Transparent Huge Pages (THP) and memory pinning.

## Memory Allocation Steps

To get predictable memory performance with THP, the allocation must follow a strict order:

* **System-Level Configuration:** Set `/sys/kernel/mm/transparent_hugepage/enabled` to `[madvise]`. This stops untracked background allocations while letting specific programs request huge pages.
* **1. Virtual Allocation (`mmap`):** Create the virtual memory space.
* **2. Hinting (`MADV_HUGEPAGE`):** Apply the kernel hint right after allocation while the address block is empty.
* **3. Preallocation (Touch Loop):** Step through the memory block at a strict **4KB page granularity**. This forces the kernel to handle physical page faults immediately, ensuring the memory is fully populated.
* **4. Memory Pinning (`mlock`):** Lock the pages in RAM. This keeps the CPU TLB cache entries active and stops the kernel from splitting 2MB pages back into 4KB pieces under memory pressure.

---

## External Collapse Strategy

QEMU cannot use `MADV_COLLAPSE` on its own during startup because `MADV_COLLAPSE` requires memory to already be present in physical RAM, or it returns an error.

To solve this on fragmented host nodes, we move the compaction step to an external handler:

* **Post-Lock Compaction:** Memory is mapped, preallocated at 4KB, and pinned with `mlock` exactly as QEMU expects.
* **Interception:** A privileged daemon (like KubeVirt's `virt-handler` or a sidecar with `CAP_SYS_NICE`) watches the process.
* **Upgrade:** The handler finds QEMU’s RAM address range and triggers `process_madvise(MADV_COLLAPSE)` from the outside.
* **Migration:** The kernel merges the pinned 4KB layouts into 2MB huge pages and transfers the lock states automatically.

---

## Available Tools

### 1. Standalone Script (`thp_allocator.py`)

A script that runs the strict allocation order. It accepts short flags for testing:

* `n` / `none`: Standard 4KB baseline.
* `hp` / `hugepage`: Asynchronous hint.
* `cl` / `collapse`: Post-lock synchronous compaction.

### 2. Matrix Runner (`test_thp_matrix.py`)

A `unittest` script that runs the allocator inside isolated subprocesses. Using separate processes ensures previous runs do not affect the next test.

### Running the Tests

Run the automated test matrix with standard verbose output:

```bash
sudo ./test_thp_matrix.py 1G --duration 5 -v

```
