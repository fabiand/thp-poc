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
        print("ERROR: Must run as root to lock memory.", file=sys.stderr)
        sys.exit(1)

def parse_size(size_str):
    size_str = size_str.upper().strip()
    units = {'G': 1024**3, 'M': 1024**2, 'K': 1024, 'B': 1}
    if size_str[-1] in units:
        return int(size_str[:-1]) * units[size_str[-1]]
    return int(size_str)

def soft_check_buddyinfo(num_bytes):
    required_blocks = (num_bytes + (2 * 1024 * 1024 - 1)) // (2 * 1024 * 1024)
    available_blocks = 0
    try:
        with open("/proc/buddyinfo", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 14:
                    available_blocks += int(parts[13])
    except Exception as e:
        print(f"[*] Skipping buddyinfo check: {e}")
        return

    if available_blocks < required_blocks:
        print(f"[!] SOFT WARNING: Requested ~{required_blocks} blocks, but only {available_blocks} available.")
    else:
        print(f"[*] Buddyinfo check: {available_blocks} 2MB blocks available (need ~{required_blocks}).")

def get_thp_coverage(start_addr):
    anon_huge_pages = 0
    in_range = False
    with open("/proc/self/smaps", "r") as f:
        for line in f:
            if "-" in line and " " in line:
                parts = line.split()
                if "-" in parts[0]:
                    vm_start, vm_end = [int(x, 16) for x in parts[0].split("-")]
                    in_range = (vm_start <= start_addr < vm_end)
            if in_range and line.startswith("AnonHugePages:"):
                anon_huge_pages += int(line.split()[1])
    return anon_huge_pages

def main():
    check_root()

    parser = argparse.ArgumentParser(description="Test locked THP memory coverage (Dynamic Order).")
    parser.add_argument("memory", help="Amount of memory to allocate (e.g., 2G, 512M)")
    parser.add_argument(
        "--madvise", 
        choices=["hugepage", "collapse"], 
        required=True, 
        help="Require 'hugepage' or 'collapse'. No default."
    )
    parser.add_argument("--duration", type=int, default=10, help="Seconds to wait (default: 10s)")
    args = parser.parse_args()

    num_bytes = parse_size(args.memory)
    advise_flag = MADV_COLLAPSE if args.madvise == "collapse" else mmap.MADV_HUGEPAGE
    flag_name = "MADV_COLLAPSE" if args.madvise == "collapse" else "MADV_HUGEPAGE"

    print(f"[*] Target allocation: {args.memory} ({num_bytes} bytes)")
    soft_check_buddyinfo(num_bytes)

    # 1. mmap (Allocate virtual address space)
    print("[*] Allocating anonymous private memory...")
    mem = mmap.mmap(-1, num_bytes, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS, prot=mmap.PROT_READ | mmap.PROT_WRITE)
    mem_address = ctypes.addressof(ctypes.c_char.from_buffer(mem))

    # 2. Case HUGEPAGE: Advise BEFORE faulting pages
    if args.madvise == "hugepage":
        print(f"[*] Advising kernel with {flag_name}...")
        try:
            mem.madvise(advise_flag)
        except OSError as e:
            print(f"[-] madvise failed: {e}")
            sys.exit(1)

    # 3. Preallocation (Touch every 4KB page)
    print("[*] Preallocating memory (faulting 4KB pages into physical RAM)...")
    for i in range(0, num_bytes, 4096):
        mem[i] = 0

    # 4. Case COLLAPSE: Advise AFTER faulting pages (synchronous merge)
    if args.madvise == "collapse":
        print(f"[*] Advising kernel with {flag_name} ({advise_flag})...")
        try:
            mem.madvise(advise_flag)
        except OSError as e:
            print(f"[-] madvise failed: {e}")
            sys.exit(1)

    # 5. mlock (Pin the final huge pages)
    print("[*] Locking memory via mlock...")
    if libc.mlock(ctypes.c_void_p(mem_address), ctypes.c_size_t(num_bytes)) != 0:
        print(f"[-] mlock failed with errno {ctypes.get_errno()}")
        sys.exit(1)

    print(f"[*] Waiting {args.duration} seconds...")
    time.sleep(args.duration)

    huge_kb = get_thp_coverage(mem_address)
    coverage = (huge_kb / (num_bytes / 1024)) * 100

    print(f"\nTarget: {num_bytes / 1024:,.0f} kB | Huge Pages: {huge_kb:,.0f} kB | Coverage: {coverage:.2f}%")
    if coverage < 95:
        print("[!] WARNING: Kernel fell back to 4KB pages.")
    else:
        print("[+] SUCCESS: Memory is backed by 2MB Huge Pages.")

if __name__ == "__main__":
    main()
