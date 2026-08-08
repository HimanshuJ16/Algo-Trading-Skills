"""
Integration tests for executing root tools and validating repository-wide behavioral constraints.
"""
import os
import subprocess
import sys
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestCoreIntegration(unittest.TestCase):
    def test_validate_skills_tool_runs(self):
        tool_path = os.path.join(ROOT_DIR, "tools", "validate_skills.py")
        res = subprocess.run([sys.executable, tool_path], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"validate_skills.py failed:\n{res.stdout}\n{res.stderr}")
        self.assertIn("All skills passed structural", res.stdout)

    def test_build_index_tool_runs(self):
        tool_path = os.path.join(ROOT_DIR, "tools", "build_index.py")
        res = subprocess.run([sys.executable, tool_path], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"build_index.py failed:\n{res.stdout}\n{res.stderr}")
        self.assertIn("Successfully generated index.json", res.stdout)


if __name__ == "__main__":
    unittest.main()
