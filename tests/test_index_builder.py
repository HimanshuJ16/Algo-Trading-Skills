"""
Unit tests for tools/build_index.py and the checked-in index.json.
"""
import json
import os
import unittest

from tools.build_index import build_index, render, INDEX_PATH
from tools.validate_skills import SUBDOMAINS

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestIndexBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = build_index()
        with open(INDEX_PATH, encoding="utf-8") as f:
            cls.checked_in_text = f.read()
        cls.checked_in = json.loads(cls.checked_in_text)

    def test_index_json_exists(self):
        self.assertTrue(os.path.isfile(INDEX_PATH), "index.json should exist at root")

    def test_index_json_schema(self):
        data = self.checked_in
        for key in ("version", "repository", "total_skills", "subdomains", "skills"):
            self.assertIn(key, data)
        self.assertNotIn("generated_at", data, "index.json must not carry a timestamp")
        self.assertGreater(data["total_skills"], 0)
        self.assertEqual(data["total_skills"], len(data["skills"]))
        for skill in data["skills"]:
            for key in ("name", "description", "domain", "subdomain", "tags", "brokers_frameworks",
                        "version", "author", "license", "path", "skill_md"):
                self.assertIn(key, skill, f"{skill.get('name')}: entry missing key '{key}'")
            self.assertIsInstance(skill["tags"], list)
            self.assertIsInstance(skill["brokers_frameworks"], list)
            self.assertIn(skill["subdomain"], SUBDOMAINS)

    def test_checked_in_index_matches_frontmatter(self):
        """index.json is generated; it must equal what build_index() produces now."""
        self.assertEqual(self.checked_in_text, render(self.index),
                         "index.json is stale -- run python tools/build_index.py")

    def test_every_skill_directory_is_indexed(self):
        dirs = sorted(d for d in os.listdir(os.path.join(ROOT_DIR, "skills"))
                      if os.path.isdir(os.path.join(ROOT_DIR, "skills", d)))
        self.assertEqual(dirs, [s["name"] for s in self.index["skills"]])

    def test_subdomain_counts_add_up(self):
        self.assertEqual(sum(self.index["subdomains"].values()), self.index["total_skills"])


if __name__ == "__main__":
    unittest.main()
