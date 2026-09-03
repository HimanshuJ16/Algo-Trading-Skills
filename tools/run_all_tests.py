#!/usr/bin/env python3
"""
Runs every skill's unittest suite (skills/*/scripts/test_*.py), each in its own
subprocess so modules cannot leak between skills and a hanging suite cannot hang CI.

    python tools/run_all_tests.py                    # everything
    python tools/run_all_tests.py --skill order-placement-idempotency --skill lookahead-bias-elimination
    python tools/run_all_tests.py --jobs 4 --timeout 120

Each subprocess runs `python -m unittest discover -s skills/<name>/scripts`, the same
command every SKILL.md quotes in its Verification section. Helper log output (the
"KILL SWITCH CALLBACK FAILED" style fixture lines) is captured and only shown for a
suite that fails. Exits non-zero on any failure, error, timeout or crash.
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT_DIR, "skills")

RAN_RE = re.compile(r"^Ran (\d+) tests? in", re.M)
OUTCOME_RE = re.compile(r"^(OK|FAILED)(?: \((.*)\))?\s*$", re.M)
MAX_OUTPUT_LINES = 400


def discover_skills(only):
    dirs = sorted(d for d in glob.glob(os.path.join(SKILLS_DIR, "*", "scripts"))
                  if glob.glob(os.path.join(d, "test_*.py")))
    if only:
        wanted = set(only)
        dirs = [d for d in dirs if os.path.basename(os.path.dirname(d)) in wanted]
        missing = wanted - {os.path.basename(os.path.dirname(d)) for d in dirs}
        if missing:
            raise SystemExit(f"no test suite for skill(s): {', '.join(sorted(missing))}")
    return dirs


def run_suite(script_dir, timeout):
    """Run one skill's suite. Returns (skill, tests_run, ok, detail, seconds)."""
    skill = os.path.basename(os.path.dirname(script_dir))
    rel = os.path.relpath(script_dir, ROOT_DIR).replace(os.sep, "/")
    cmd = [sys.executable, "-B", "-m", "unittest", "discover", "-s", rel, "-p", "test_*.py"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    start = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT_DIR, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "")
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return skill, 0, False, f"TIMEOUT after {timeout}s\n{out}", time.time() - start
    output = proc.stdout + proc.stderr
    ran = RAN_RE.findall(output)
    tests_run = int(ran[-1]) if ran else 0
    outcome = OUTCOME_RE.findall(output)
    ok = proc.returncode == 0 and bool(outcome) and outcome[-1][0] == "OK"
    detail = "" if ok else (outcome[-1][1] if outcome and outcome[-1][1] else f"exit code {proc.returncode}")
    if not ok:
        detail += "\n" + output
    return skill, tests_run, ok, detail, time.time() - start


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skill", action="append", help="run only this skill (repeatable)")
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 2), help="parallel suites")
    parser.add_argument("--timeout", type=int, default=120, help="seconds per skill suite")
    parser.add_argument("--quiet", action="store_true", help="print only failures and the summary")
    args = parser.parse_args(argv)

    suites = discover_skills(args.skill)
    if not suites:
        print("No test suites found under skills/*/scripts/test_*.py")
        return 1
    print(f"Found {len(suites)} skill test suites. Running with {args.jobs} workers, "
          f"{args.timeout}s timeout per suite.\n" + "=" * 60)

    start = time.time()
    total = 0
    failures = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for skill, n, ok, detail, secs in pool.map(lambda d: run_suite(d, args.timeout), suites):
            total += n
            if ok:
                if not args.quiet:
                    print(f"  [PASS] {skill} ({n} tests, {secs:.1f}s)")
            else:
                failures.append((skill, detail))
                print(f"  [FAIL] {skill} ({n} tests run, {secs:.1f}s)")

    print("=" * 60)
    print(f"Summary: Executed {total} tests across {len(suites)} skill suites in {time.time() - start:.2f}s.")
    if failures:
        print(f"\n[FAIL] {len(failures)} suite(s) failed:")
        for skill, detail in failures:
            lines = detail.rstrip().splitlines()
            if len(lines) > MAX_OUTPUT_LINES:
                lines = lines[:MAX_OUTPUT_LINES] + [f"... ({len(lines) - MAX_OUTPUT_LINES} more lines)"]
            print(f"\n--- {skill} ---")
            print("\n".join(lines))
            print(f"Re-run alone: python -m unittest discover -s skills/{skill}/scripts -v")
        return 1
    print("\n[SUCCESS] All unit tests passed cleanly!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
