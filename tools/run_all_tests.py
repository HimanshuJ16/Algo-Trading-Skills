#!/usr/bin/env python3
"""
Discovers and executes every test_*.py script across all skills under skills/*/scripts/.

Run locally with: python tools/run_all_tests.py
Exits non-zero on any test failure or error so CI fails the build.
"""
import glob
import importlib.util
import os
import sys
import time
import unittest

import io

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT_DIR, "skills")


def run_all_tests():
    test_files = sorted(glob.glob(os.path.join(SKILLS_DIR, "*", "scripts", "test_*.py")))

    if not test_files:
        print("No test files found under skills/*/scripts/test_*.py!")
        sys.exit(1)

    print(f"Found {len(test_files)} test files across skills.")
    print("Executing full unit test suite...\n" + "=" * 60)

    start_time = time.time()
    total_runs = 0
    total_failures = 0
    total_errors = 0
    failed_files = []
    devnull_stream = io.StringIO()

    for test_file in test_files:
        rel_path = os.path.relpath(test_file, ROOT_DIR)
        script_dir = os.path.dirname(test_file)

        # Track modules loaded before running this test file to purge afterwards
        pre_modules = set(sys.modules.keys())

        # Ensure module dependencies in the script directory can be imported
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        module_name = os.path.splitext(os.path.basename(test_file))[0]

        try:
            loader = unittest.TestLoader()
            suite = loader.discover(script_dir, pattern=os.path.basename(test_file))
            runner = unittest.TextTestRunner(stream=devnull_stream, verbosity=0)
            result = runner.run(suite)

            total_runs += result.testsRun
            num_failures = len(result.failures)
            num_errors = len(result.errors)

            if num_failures > 0 or num_errors > 0:
                total_failures += num_failures
                total_errors += num_errors
                failed_files.append((rel_path, result.failures, result.errors))
                print(f"  [FAIL] {rel_path} ({result.testsRun} tests run, {num_failures} failures, {num_errors} errors)")
            else:
                print(f"  [PASS] {rel_path} ({result.testsRun} tests)")

        except Exception as e:
            total_errors += 1
            failed_files.append((rel_path, [], [str(e)]))
            print(f"  [ERROR] {rel_path}: {e}")

        # Clean up sys.path and sys.modules loaded during this test run
        if script_dir in sys.path:
            sys.path.remove(script_dir)
        new_modules = set(sys.modules.keys()) - pre_modules
        for mod in new_modules:
            if not any(mod.startswith(pkg) for pkg in ("numpy", "pandas", "scipy", "sklearn", "pyotp", "yaml")):
                sys.modules.pop(mod, None)

    duration = time.time() - start_time
    print("=" * 60)
    print(f"Summary: Executed {total_runs} tests across {len(test_files)} files in {duration:.2f}s.")

    if failed_files:
        print(f"\n[FAIL] {len(failed_files)} test file(s) failed:")
        for rel_path, failures, errors in failed_files:
            print(f"  - {rel_path}")
            for f in failures:
                print(f"      Failure: {f[0]} -> {f[1].splitlines()[-1]}")
            for e in errors:
                err_msg = str(e[1]).splitlines()[-1] if isinstance(e, tuple) else str(e)
                print(f"      Error: {e[0] if isinstance(e, tuple) else ''} -> {err_msg}")
        sys.exit(1)

    print("\n[SUCCESS] All unit tests passed cleanly!")
    sys.exit(0)


if __name__ == "__main__":
    run_all_tests()
