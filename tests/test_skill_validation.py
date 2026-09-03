"""
Behavioral and structural unit tests for tools/validate_skills.py.
"""
import os
import shutil
import tempfile
import unittest

from tools.validate_skills import (
    validate_skill_dir,
    CANONICAL_TEST_CMD,
    REQUIRED_FRONTMATTER_FIELDS,
    REQUIRED_SECTIONS,
    REQUIRED_SUBDIRS,
)


class TestSkillValidation(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    @staticmethod
    def _body(name):
        """A body carrying every required section, with a runnable Verification."""
        sections = []
        for sec in REQUIRED_SECTIONS:
            if sec == "Verification":
                cmd = CANONICAL_TEST_CMD.format(name=name)
                sections.append(f"## {sec}\n- Run `{cmd}`.")
            else:
                sections.append(f"## {sec}\nContent for {sec}")
        return "\n".join(sections)

    def _create_valid_skill(self, name="test-skill"):
        skill_dir = os.path.join(self.test_dir, name)
        os.makedirs(skill_dir)
        for sd in REQUIRED_SUBDIRS:
            os.makedirs(os.path.join(skill_dir, sd))
            with open(os.path.join(skill_dir, sd, "dummy.txt"), "w", encoding="utf-8") as f:
                f.write("content")

        # scripts/ must hold a helper module plus its test suite, distinguishable by name.
        with open(os.path.join(skill_dir, "scripts", "helper.py"), "w", encoding="utf-8") as f:
            f.write("VALUE = 1\n")
        with open(os.path.join(skill_dir, "scripts", "test_helper.py"), "w", encoding="utf-8") as f:
            f.write("import helper\n")

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
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(frontmatter + self._body(name))
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
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(frontmatter + self._body("field-missing"))

        errors = validate_skill_dir(skill_dir, {"field-missing"})
        self.assertTrue(any("missing required field 'author'" in e for e in errors))

    def test_missing_subdirectory_fails(self):
        skill_dir = self._create_valid_skill("sub-missing")
        shutil.rmtree(os.path.join(skill_dir, "references"))
        errors = validate_skill_dir(skill_dir, {"sub-missing"})
        self.assertTrue(any("missing required subdirectory 'references/'" in e for e in errors))

    def test_helper_named_like_a_test_fails(self):
        """run_all_tests.py globs test_*.py, so a helper matching it is collected as a suite."""
        skill_dir = self._create_valid_skill("helper-named-test")
        scripts = os.path.join(skill_dir, "scripts")
        os.rename(os.path.join(scripts, "helper.py"),
                  os.path.join(scripts, "test_helper_module.py"))
        errors = validate_skill_dir(skill_dir, {"helper-named-test"})
        self.assertTrue(any("no helper module" in e for e in errors))

    def test_missing_test_suite_fails(self):
        skill_dir = self._create_valid_skill("no-suite")
        os.remove(os.path.join(skill_dir, "scripts", "test_helper.py"))
        errors = validate_skill_dir(skill_dir, {"no-suite"})
        self.assertTrue(any("no test_*.py suite" in e for e in errors))

    def test_verification_without_test_command_fails(self):
        skill_dir = self._create_valid_skill("no-cmd")
        path = os.path.join(skill_dir, "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        text = text.replace(f"- Run `{CANONICAL_TEST_CMD.format(name='no-cmd')}`.",
                            "- Eyeball the output.")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        errors = validate_skill_dir(skill_dir, {"no-cmd"})
        self.assertTrue(any("never shows how to run the tests" in e for e in errors))

    def test_cwd_relative_test_command_fails(self):
        """`python scripts/test_x.py` does not resolve from the repository root."""
        skill_dir = self._create_valid_skill("relative-cmd")
        path = os.path.join(skill_dir, "assets", "checklist.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("- [ ] Run `python scripts/test_helper.py`.\n")
        errors = validate_skill_dir(skill_dir, {"relative-cmd"})
        self.assertTrue(any("does not run from the repository root" in e for e in errors))

    def test_whole_repo_runner_is_allowed(self):
        skill_dir = self._create_valid_skill("repo-runner")
        path = os.path.join(skill_dir, "assets", "checklist.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("- [ ] Run `python tools/run_all_tests.py`.\n")
        errors = validate_skill_dir(skill_dir, {"repo-runner"})
        self.assertEqual(errors, [])

    def test_verbose_flag_is_allowed(self):
        skill_dir = self._create_valid_skill("verbose-cmd")
        path = os.path.join(skill_dir, "references", "workflows.md")
        cmd = CANONICAL_TEST_CMD.format(name="verbose-cmd")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Run `{cmd} -v` for per-test output.\n")
        errors = validate_skill_dir(skill_dir, {"verbose-cmd"})
        self.assertEqual(errors, [])

    def test_line_wrapped_command_is_allowed(self):
        """Markdown wraps long commands; the check compares on collapsed whitespace."""
        skill_dir = self._create_valid_skill("wrapped-cmd")
        path = os.path.join(skill_dir, "assets", "checklist.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("- [ ] Run\n      `python -m unittest discover -s\n"
                    "      skills/wrapped-cmd/scripts`.\n")
        errors = validate_skill_dir(skill_dir, {"wrapped-cmd"})
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
