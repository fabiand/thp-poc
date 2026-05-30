#!/usr/bin/env python3
import sys
import os
import time
import ctypes
import argparse

# Linux syscall numbers for x86_64
SYS_PIDFD_OPEN = 434
SYS_PROCESS_MADVISE = 440
MADV_COLLAPSE = 25

libc = ctypes.CDLL("libc.so.6", use_errno=True)

class iovec(ctypes.Structure):
    _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]

def find_qemu_pid():
    """Scans /proc to find the active QEMU process."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "r") as f:
                cmd = f.read()
                if "qemu-system" in cmd:
                    return int(pid)
        except IOError:
            continue
    return None

def find_guest_ram_vma(pid, target_bytes):
    """Finds the virtual memory address range matching the guest RAM allocation size."""
    try:
        with open(f"/proc/{pid}/maps", "r") as f:
            for line in f:
                # Look for massive anonymous private maps
                if "anon" in line or "/" not in line:
                    parts = line.split()
                    addr_range = parts[0]
                    start, end = [int(x, 16) for x in addr_range.split("-")]
                    size = end - start
                    # Give a small buffer check around the exact target allocation
                    if abs(size - target_bytes) < (32 * 1024 * 1024):
                        return start, size
    except IOError as e:
        print(f"[-] Failed to read maps for PID {pid}: {e}")
    return None, None

def main():
    parser = argparse.ArgumentParser(description="KubeVirt External THP Collapse Sidecar Daemon")
    parser.add_argument("--target-gb", type=int, required=True, help="Target VM memory size in GB")
    args = parser.parse_args()

    target_bytes = args.target_gb * 1024 * 1024 * 1024

    print("[*] Sidecar daemon started. Watching for QEMU process...")
    qemu_pid = None
    
    # Loop until QEMU initializes and boots
    while not qemu_pid:
        qemu_pid = find_qemu_pid()
        if not qemu_pid:
            time.sleep(0.5)

    print(f"[+] Found QEMU process at PID: {qemu_pid}")

    # Wait for QEMU to finish its internal 4KB preallocation loop
    time.sleep(2) 

    # Find the address block mapping the Guest RAM
    start_addr, block_size = find_guest_ram_vma(qemu_pid, target_bytes)
    if not start_addr:
        print("[-] ERROR: Could not locate the guest RAM virtual memory region.", file=sys.stderr)
        sys.exit(1)

    print(f"[+] Found Target RAM range: {hex(start_addr)} ({block_size} bytes)")

    # 1. Get a file descriptor for QEMU's process context
    pidfd = libc.syscall(SYS_PIDFD_OPEN, qemu_pid, 0)
    if pidfd < 0:
        print(f"[-] pidfd_open failed, errno: {ctypes.get_errno()}", file=sys.stderr)
        sys.exit(1)

    # 2. Define the target memory vector block
    iov = iovec()
    iov.iov_base = ctypes.c_void_p(start_addr)
    iov.iov_len = ctypes.c_size_t(block_size)

    # 3. Issue the remote cross-process madvise collapse
    print(f"[*] Executing remote process_madvise(MADV_COLLAPSE) on PID {qemu_pid}...")
    ret = libc.syscall(SYS_PROCESS_MADVISE, pidfd, ctypes.byref(iov), 1, MADV_COLLAPSE, 0)

    if ret < 0:
        print(f"[-] process_madvise failed, errno: {ctypes.get_errno()}", file=sys.stderr)
        os.close(pidfd)
        sys.exit(1)

    print("[+] SUCCESS: Locked memory ranges successfully collapsed into 2MB THP blocks.")
    os.close(pidfd)

if __name__ == "__main__":
    main()
