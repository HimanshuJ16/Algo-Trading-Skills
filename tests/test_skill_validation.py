"""
Behavioral and structural unit tests for tools/validate_skills.py.
"""
import os
import shutil
import tempfile
import unittest

from tools.validate_skills import (
    validate_skill_dir,
    REQUIRED_FRONTMATTER_FIELDS,
    REQUIRED_SECTIONS,
    REQUIRED_SUBDIRS,
)


class TestSkillValidation(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _create_valid_skill(self, name="test-skill"):
        skill_dir = os.path.join(self.test_dir, name)
        os.makedirs(skill_dir)
        for sd in REQUIRED_SUBDIRS:
            os.makedirs(os.path.join(skill_dir, sd))
            with open(os.path.join(skill_dir, sd, "dummy.txt"), "w", encoding="utf-8") as f:
                f.write("content")

        frontmatter = (
            "---\n"
            f"name: {name}\n"
            "description: Test skill description\n"
            "domain: algorithmic-trading\n"
            "subdomain: risk-management\n"
            "tags: [test, risk]\n"
            "brokers_frameworks: [Universal]\n"
            "version: 1.0.0\n"
            "author: test-author\n"
            "license: Apache-2.0\n"
            "---\n"
        )
        body = "\n".join([f"## {sec}\nContent for {sec}" for sec in REQUIRED_SECTIONS])
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(frontmatter + body)
        return skill_dir

    def test_valid_skill_passes(self):
        skill_dir = self._create_valid_skill("valid-skill")
        errors = validate_skill_dir(skill_dir, {"valid-skill"})
        self.assertEqual(errors, [])

    def test_missing_skill_md_fails(self):
        skill_dir = os.path.join(self.test_dir, "missing-md")
        os.makedirs(skill_dir)
        errors = validate_skill_dir(skill_dir, set())
        self.assertTrue(any("missing SKILL.md" in e for e in errors))

    def test_missing_frontmatter_fails(self):
        skill_dir = os.path.join(self.test_dir, "no-fm")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("No frontmatter block here")
        errors = validate_skill_dir(skill_dir, set())
        self.assertTrue(any("missing YAML frontmatter" in e for e in errors))

    def test_missing_required_field_fails(self):
        skill_dir = self._create_valid_skill("field-missing")
        # Overwrite SKILL.md without 'author'
        frontmatter = (
            "---\n"
            "name: field-missing\n"
            "description: Test skill\n"
            "domain: algorithmic-trading\n"
            "subdomain: risk-management\n"
            "tags: [test]\n"
            "brokers_frameworks: [Universal]\n"
            "version: 1.0.0\n"
            "license: Apache-2.0\n"
            "---\n"
        )
        body = "\n".join([f"## {sec}\nContent" for sec in REQUIRED_SECTIONS])
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(frontmatter + body)

        errors = validate_skill_dir(skill_dir, {"field-missing"})
        self.assertTrue(any("missing required field 'author'" in e for e in errors))

    def test_missing_subdirectory_fails(self):
        skill_dir = self._create_valid_skill("sub-missing")
        shutil.rmtree(os.path.join(skill_dir, "references"))
        errors = validate_skill_dir(skill_dir, {"sub-missing"})
        self.assertTrue(any("missing required subdirectory 'references/'" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
