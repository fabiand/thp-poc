#!/usr/bin/env python3
import sys
import os
import time
import mmap
import ctypes
import argparse

libc = ctypes.CDLL("libc.so.6", use_errno=True)
MADV_COLLAPSE = getattr(mmap, "MADV_COLLAPSE", 25)

def check_root():
    if os.geteuid() != 0:
        print("ERROR: Must run as root to lock memory and inspect smaps.", file=sys.stderr)
        sys.exit(1)

def check_thp_madvise():
    path = "/sys/kernel/mm/transparent_hugepage/enabled"
    with open(path, "r") as f:
        if "[madvise]" not in f.read():
            print(f"ERROR: THP not configured to 'madvise'.", file=sys.stderr)
            sys.exit(1)

def parse_size(size_str):
    size_str = size_str.upper().strip()
    units = {'G': 1024**3, 'M': 1024**2, 'K': 1024, 'B': 1}
    if size_str[-1] in units:
        return int(size_str[:-1]) * units[size_str[-1]]
    return int(size_str)

def get_thp_and_locked_status(start_addr):
    anon_huge_pages = 0
    locked_pages = 0
    in_range = False
    with open("/proc/self/smaps", "r") as f:
        for line in f:
            if "-" in line and " " in line:
                parts = line.split()
                if "-" in parts[0]:
                    vm_start, vm_end = [int(x, 16) for x in parts[0].split("-")]
                    in_range = (vm_start <= start_addr < vm_end)
            if in_range:
                if line.startswith("AnonHugePages:"):
                    anon_huge_pages += int(line.split()[1])
                elif line.startswith("Locked:"):
                    locked_pages += int(line.split()[1])
    return anon_huge_pages, locked_pages

def verify_locked_thp_efficiency(label, start_addr, target_bytes):
    huge_kb, locked_kb = get_thp_and_locked_status(start_addr)
    target_kb = target_bytes / 1024
    
    thp_of_locked_pct = (huge_kb / locked_kb * 100) if locked_kb > 0 else 0
    overall_coverage = (huge_kb / target_kb * 100)
    
    print(f"\nCHECKPOINT [{label}]:")
    print(f" -> Total Locked Memory: {locked_kb:,.0f} kB / {target_kb:,.0f} kB")
    print(f" -> Of that, THP Backed: {huge_kb:,.0f} kB ({thp_of_locked_pct:.2f}%)")
    print(f"CHECKPOINT [{label}]: {overall_coverage:.2f}%")

def main():
    check_root()
    check_thp_madvise()

    parser = argparse.ArgumentParser(description="Standalone THP Allocator Utility")
    parser.add_argument("memory", help="Memory size (e.g., 512M, 1G)")
    parser.add_argument(
        "--madvise", 
        choices=["none", "n", "hugepage", "hp", "collapse", "cl"], 
        required=True,
        help="THP strategy. Options: none (n), hugepage (hp), collapse (cl)"
    )
    parser.add_argument("--duration", type=int, default=10)
    args = parser.parse_args()

    # Normalize abbreviations to standard strings
    strategy_map = {
        "n": "none", "none": "none",
        "hp": "hugepage", "hugepage": "hugepage",
        "cl": "collapse", "collapse": "collapse"
    }
    madv_strategy = strategy_map[args.madvise.lower()]
    num_bytes = parse_size(args.memory)
    
    # 1. mmap
    mem = mmap.mmap(-1, num_bytes, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS, prot=mmap.PROT_READ | mmap.PROT_WRITE)
    mem_address = ctypes.addressof(ctypes.c_char.from_buffer(mem))

    # 2. Pre-madvise (Async HUGEPAGE)
    if madv_strategy == "hugepage":
        mem.madvise(mmap.MADV_HUGEPAGE)

    # 3. Preallocation (QEMU 4KB Touch loop)
    for i in range(0, num_bytes, 4096):
        mem[i] = 0

    # 4. mlock
    if libc.mlock(ctypes.c_void_p(mem_address), ctypes.c_size_t(num_bytes)) != 0:
        print(f"[-] mlock failed with errno {ctypes.get_errno()}", file=sys.stderr)
        sys.exit(1)

    # 5. Post-mlock / Post-alloc madvise (Sync COLLAPSE)
    if madv_strategy == "collapse":
        try:
            mem.madvise(MADV_COLLAPSE)
        except OSError as e:
            print(f"[-] MADV_COLLAPSE failed: {e}", file=sys.stderr)

    verify_locked_thp_efficiency("IMMEDIATE", mem_address, num_bytes)
    time.sleep(args.duration)
    verify_locked_thp_efficiency("FINAL", mem_address, num_bytes)

    # Cleanup
    libc.munlock(ctypes.c_void_p(mem_address), ctypes.c_size_t(num_bytes))
    mem.close()

if __name__ == "__main__":
    main()
