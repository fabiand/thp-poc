# THP Memory Allocation Strategy & Verification Suite

This repository contains tools to validate, benchmark, and emulate the precise host-level memory lifecycle used by hypervisors (like QEMU) and orchestrators (like KubeVirt) when utilizing Transparent Huge Pages (THP) alongside memory pinning.

## Core Allocation Lifecycle

To achieve deterministic memory performance with THP, the allocation sequence must follow a strict operational order:

* **System-Level `madvise`:** The host must have `/sys/kernel/mm/transparent_hugepage/enabled` configured strictly to `[madvise]`. This prevents global untracked background allocations while allowing targeted workloads to explicitly request huge pages.
* **1. Virtual Allocation (`mmap`):** Draw the boundaries for anonymous private memory space.
* **2. Async Hinting (`MADV_HUGEPAGE`):** Apply the kernel hint right after allocation while the address block is still empty.
* **3. Eager Preallocation (The Touch Loop):** Iterate through the memory block at a strict **4KB page granularity**. This forces the kernel to process physical page faults at allocation time, eliminating lazy mapping traps and ensuring full density.
* **4. Memory Pinning (`mlock`):** Secure the fully materialized pages in physical RAM. This anchors the physical path, keeps CPU TLB cache entries hot, and completely blocks the kernel from splitting 2MB structures back into 4KB pieces under system memory pressure.

---

## The External Collapse Strategy

Upstream QEMU cannot natively use `MADV_COLLAPSE` for VM memory initialization due to a Catch-22: `MADV_COLLAPSE` is a synchronous command that fails with `EINVAL` if the targeted memory range isn't already resident in physical RAM.

To circumvent this limitation on fragmented host nodes, we offload the compaction step to an infrastructure handler or external sidecar:

* **Post-Lock Compaction:** The memory is mapped, preallocated at 4KB, and pinned with `mlock` exactly how QEMU expects.
* **`process_madvise` Interception:** An external privileged daemon (like KubeVirt's `virt-handler` using host root permissions, or a sidecar container using `CAP_SYS_NICE`) watches the process.
* **Synchronous Upgrade:** The handler locates QEMU’s RAM address range from the host namespace and triggers a remote `process_madvise(MADV_COLLAPSE)`.
* **Atomic Migration:** The kernel atomically migrates the pinned 4KB layouts into cohesive 2MB huge pages under the hood, transferring the lock states seamlessly without QEMU even realizing its underlying structure was upgraded.

---

## Included Utilities

### 1. Standalone Utility (`thp_allocator.py`)

A single-purpose memory engine executing the strict QEMU lifecycle order. Features abbreviation flags for quick evaluation:

* `n` / `none`: Standard 4KB baseline footprint.
* `hp` / `hugepage`: Asynchronous forward-looking hint.
* `cl` / `collapse`: Post-lock synchronous compaction validation.

### 2. Matrix Verification Engine (`test_thp_matrix.py`)

A standard Python `unittest` suite that orchestrates the core allocator inside completely isolated subprocesses. Running via subprocess ensures previous runs never introduce memory footprint contamination.

### Execution

Run the automated matrix suite with standard verbose output:

```bash
sudo ./test_thp_matrix.py 1G --duration 5 -v

```
