#!/usr/bin/env python3
import sys
import os
import re
import subprocess
import argparse
import unittest

# Global metrics collection for the final table rendering
MATRIX_RESULTS = []
TARGET_MEMORY = "512M"
SETTLE_DURATION = "3"

class TestTHPAllocatingMatrix(unittest.TestCase):

    @classmethod
    def tearDownClass(cls):
        """Builds a scannable structural summary report of all matrix outcomes."""
        print("\n==================== MATRIX SUMMARY ====================")
        print(f"{'Strategy Combination':<32} | {'Immediate':<10} | {'Post-Settle':<11}")
        print("-" * 61)
        for name, imm_res, final_res in MATRIX_RESULTS:
            print(f"{name:<32} | {imm_res:<10} | {final_res:<11}")
        print("========================================================")

    def run_strategy(self, display_name, madv_flag):
        """Invokes the standalone sub-allocator in a clean, isolated host process."""
        script_path = os.path.join(os.path.dirname(__file__), "thp_allocator.py")
        
        cmd = [
            sys.executable, script_path, TARGET_MEMORY,
            "--madvise", madv_flag,
            "--duration", str(SETTLE_DURATION)
        ]
        
        # Execute process safely with captured outputs
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        # Assert clean runtime execution
        self.assertEqual(res.returncode, 0, f"Allocator crashed: {res.stderr}")

        # Parse coverage string metrics via regular expressions
        imm_match = re.search(r"CHECKPOINT \[IMMEDIATE\]:\s*([\d.]+)%", res.stdout)
        final_match = re.search(r"CHECKPOINT \[FINAL\]:\s*([\d.]+)%", res.stdout)
        
        imm_pct = f"{imm_match.group(1)}%" if imm_match else "N/A"
        final_pct = f"{final_match.group(1)}%" if final_match else "N/A"

        MATRIX_RESULTS.append((display_name, imm_pct, final_pct))

    def test_1_baseline(self):
        self.run_strategy("Baseline (No Advice)", "none")

    def test_2_async_hint(self):
        self.run_strategy("Async Hint Only (HUGEPAGE)", "hugepage")

    def test_3_sync_force(self):
        self.run_strategy("Sync Force Only (COLLAPSE)", "collapse")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Matrix Runner Custom Argument Interceptor", add_help=False)
    parser.add_argument("memory", nargs="?", default="512M")
    parser.add_argument("--duration", type=int, default=3)
    parser.add_argument("-h", "--help", action="store_true")
    args, unknown = parser.parse_known_args()

    if args.help:
        parser.print_help()
        sys.exit(0)

    TARGET_MEMORY = args.memory
    SETTLE_DURATION = args.duration

    # Drop our intercepted parameters so unittest main doesn't raise parsing errors
    sys.argv = [sys.argv[0]] + unknown
    unittest.main()
