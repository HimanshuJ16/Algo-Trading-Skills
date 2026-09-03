"""
Integration tests that run the repository tools end to end. None of them write to a
tracked file: index output goes to a temporary directory, and the checked-in
index.json and marketplace.json are only compared, never rewritten.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT_DIR, "tools")


def run(*args):
    return subprocess.run([sys.executable, *args], cwd=ROOT_DIR, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


class TestCoreIntegration(unittest.TestCase):
    def test_validate_skills_tool_runs(self):
        res = run(os.path.join(TOOLS, "validate_skills.py"))
        self.assertEqual(res.returncode, 0, f"validate_skills.py failed:\n{res.stdout}\n{res.stderr}")
        self.assertIn("All skills passed", res.stdout)

    def test_build_index_writes_to_a_chosen_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "index.json")
            res = run(os.path.join(TOOLS, "build_index.py"), "--output", out)
            self.assertEqual(res.returncode, 0, f"build_index.py failed:\n{res.stdout}\n{res.stderr}")
            self.assertTrue(os.path.isfile(out))
            self.assertIn("Successfully generated", res.stdout)

    def test_checked_in_index_is_current(self):
        res = run(os.path.join(TOOLS, "build_index.py"), "--check")
        self.assertEqual(res.returncode, 0, f"index.json is stale:\n{res.stdout}\n{res.stderr}")

    def test_checked_in_marketplace_is_current(self):
        res = run(os.path.join(TOOLS, "build_marketplace.py"), "--check")
        self.assertEqual(res.returncode, 0, f"marketplace.json is stale:\n{res.stdout}\n{res.stderr}")

    def test_runner_filters_to_one_skill(self):
        res = run(os.path.join(TOOLS, "run_all_tests.py"), "--skill", "order-placement-idempotency", "--quiet")
        self.assertEqual(res.returncode, 0, f"run_all_tests.py failed:\n{res.stdout}\n{res.stderr}")
        self.assertIn("across 1 skill suites", res.stdout)

    def test_runner_reports_unknown_skill(self):
        res = run(os.path.join(TOOLS, "run_all_tests.py"), "--skill", "no-such-skill")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("no test suite for skill", res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
