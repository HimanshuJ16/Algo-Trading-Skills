"""
Unit tests for tools/build_index.py and repository index integrity.
"""
import json
import os
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(ROOT_DIR, "index.json")


class TestIndexBuilder(unittest.TestCase):
    def test_index_json_exists(self):
        self.assertTrue(os.path.isfile(INDEX_PATH), "index.json should exist at root")

    def test_index_json_schema(self):
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("version", data)
        self.assertIn("total_skills", data)
        self.assertIn("skills", data)
        self.assertGreater(data["total_skills"], 0)
        self.assertEqual(data["total_skills"], len(data["skills"]))

        first_skill = data["skills"][0]
        required_keys = ["name", "description", "domain", "subdomain", "tags", "path", "skill_md"]
        for key in required_keys:
            self.assertIn(key, first_skill, f"Skill entry missing key '{key}'")


if __name__ == "__main__":
    unittest.main()
