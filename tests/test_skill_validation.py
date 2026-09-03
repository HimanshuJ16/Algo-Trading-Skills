"""
Behavioral unit tests for tools/validate_skills.py.
"""
import os
import shutil
import tempfile
import unittest

from tools.validate_skills import (
    validate_skill_dir,
    parse_frontmatter,
    CANONICAL_TEST_CMD,
    DESCRIPTION_MAX,
    REQUIRED_FRONTMATTER_FIELDS,
    REQUIRED_METADATA_FIELDS,
    REQUIRED_SECTIONS,
    REQUIRED_SUBDIRS,
    REQUIRED_FILES,
)


def frontmatter(name, **overrides):
    """A spec-conformant frontmatter block; overrides replace metadata or top-level keys."""
    top = {
        "name": name,
        "description": "Use when a test needs a spec-conformant skill fixture to validate against",
        "license": "Apache-2.0",
    }
    meta = {
        "domain": "algorithmic-trading",
        "subdomain": "risk-management",
        "tags": "test, risk",
        "brokers_frameworks": "Universal",
        "version": '"1.0.0"',
        "author": "algo-trading-skills-contributors",
    }
    for k, v in overrides.items():
        if k in meta:
            meta[k] = v
        else:
            top[k] = v
    lines = ["---"]
    for k, v in top.items():
        if v is not None:
            lines.append(f"{k}: {v}")
    lines.append("metadata:")
    for k, v in meta.items():
        if v is not None:
            lines.append(f"  {k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


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

    def _create_valid_skill(self, name="test-skill", fm=None):
        skill_dir = os.path.join(self.test_dir, name)
        os.makedirs(skill_dir)
        for sd in REQUIRED_SUBDIRS:
            os.makedirs(os.path.join(skill_dir, sd))
        for rel in REQUIRED_FILES:
            with open(os.path.join(skill_dir, rel), "w", encoding="utf-8") as f:
                f.write("content\n")
        with open(os.path.join(skill_dir, "scripts", "helper.py"), "w", encoding="utf-8") as f:
            f.write("VALUE = 1\n")
        with open(os.path.join(skill_dir, "scripts", "test_helper.py"), "w", encoding="utf-8") as f:
            f.write("import helper\n")
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write((fm or frontmatter(name)) + self._body(name))
        return skill_dir

    def _errors(self, name, **overrides):
        skill_dir = self._create_valid_skill(name, frontmatter(name, **overrides))
        return validate_skill_dir(skill_dir, {name})

    def test_valid_skill_passes(self):
        skill_dir = self._create_valid_skill("valid-skill")
        self.assertEqual(validate_skill_dir(skill_dir, {"valid-skill"}), [])

    def test_parse_frontmatter_handles_crlf(self):
        fm, body = parse_frontmatter(frontmatter("x-y-z").replace("\n", "\r\n") + "body")
        self.assertEqual(fm["name"], "x-y-z")
        self.assertEqual(fm["metadata"]["subdomain"], "risk-management")
        self.assertEqual(body, "body")

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
        errors = self._errors("field-missing", author=None)
        self.assertTrue(any("metadata missing required field 'author'" in e for e in errors))
        self.assertIn("author", REQUIRED_METADATA_FIELDS)
        self.assertIn("metadata", REQUIRED_FRONTMATTER_FIELDS)

    def test_repo_field_at_top_level_fails(self):
        """agentskills.io allows only name/description/license/compatibility/allowed-tools/metadata."""
        errors = self._errors("top-level-tags", tags2=None)  # no-op override
        self.assertEqual(errors, [])
        skill_dir = os.path.join(self.test_dir, "top-level-tags")
        path = os.path.join(skill_dir, "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace("license: Apache-2.0\n", "license: Apache-2.0\ntags: [a, b]\n"))
        errors = validate_skill_dir(skill_dir, {"top-level-tags"})
        self.assertTrue(any("unexpected top-level frontmatter field" in e for e in errors))

    def test_unknown_subdomain_fails(self):
        errors = self._errors("bad-subdomain", subdomain="Quantitative Wizardry")
        self.assertTrue(any("not one of the 16 domains" in e for e in errors))

    def test_wrong_license_or_author_fails(self):
        errors = self._errors("mit-skill", license="MIT")
        self.assertTrue(any("license must be 'Apache-2.0'" in e for e in errors))
        errors = self._errors("odd-author", author="Quant Team")
        self.assertTrue(any("metadata.author must be" in e for e in errors))

    def test_description_must_be_a_trigger_and_short(self):
        errors = self._errors("noun-desc", description="Institutional compliance skill for reg reporting")
        self.assertTrue(any("must start with 'Use when" in e for e in errors))
        self.assertTrue(any("marketing word" in e for e in errors))
        errors = self._errors("long-desc", description="Use when " + "x" * DESCRIPTION_MAX)
        self.assertTrue(any("maximum is" in e for e in errors))

    def test_unquoted_float_version_fails(self):
        errors = self._errors("float-version", version="1.10")
        self.assertTrue(any("must be a string" in e for e in errors))

    def test_name_length_and_shape(self):
        long_name = "a" * 65
        skill_dir = self._create_valid_skill(long_name, frontmatter(long_name))
        errors = validate_skill_dir(skill_dir, {long_name})
        self.assertTrue(any("at most 64 chars" in e for e in errors))

    def test_section_heading_must_be_level_two(self):
        skill_dir = self._create_valid_skill("h3-section")
        path = os.path.join(skill_dir, "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace("## Common Pitfalls", "### Common Pitfalls"))
        errors = validate_skill_dir(skill_dir, {"h3-section"})
        self.assertTrue(any("missing required section '## Common Pitfalls'" in e for e in errors))

    def test_broken_related_skill_reference_fails(self):
        skill_dir = self._create_valid_skill("has-broken-ref")
        path = os.path.join(skill_dir, "SKILL.md")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n- `no-such-skill-here`\n")
        errors = validate_skill_dir(skill_dir, {"has-broken-ref"})
        self.assertTrue(any("broken Related Skills reference `no-such-skill-here`" in e for e in errors))

    def test_missing_subdirectory_fails(self):
        skill_dir = self._create_valid_skill("sub-missing")
        shutil.rmtree(os.path.join(skill_dir, "references"))
        errors = validate_skill_dir(skill_dir, {"sub-missing"})
        self.assertTrue(any("missing required subdirectory 'references/'" in e for e in errors))

    def test_missing_named_file_fails(self):
        skill_dir = self._create_valid_skill("no-checklist")
        os.remove(os.path.join(skill_dir, "assets", "checklist.md"))
        with open(os.path.join(skill_dir, "assets", "other.md"), "w", encoding="utf-8") as f:
            f.write("x\n")
        errors = validate_skill_dir(skill_dir, {"no-checklist"})
        self.assertTrue(any("missing required file 'assets/checklist.md'" in e for e in errors))

    def test_helper_named_like_a_test_fails(self):
        """run_all_tests.py globs test_*.py, so a helper matching it is collected as a suite."""
        skill_dir = self._create_valid_skill("helper-named-test")
        scripts = os.path.join(skill_dir, "scripts")
        os.rename(os.path.join(scripts, "helper.py"), os.path.join(scripts, "test_helper_module.py"))
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
        text = text.replace(f"- Run `{CANONICAL_TEST_CMD.format(name='no-cmd')}`.", "- Eyeball the output.")
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

    def test_python3_and_pytest_forms_are_caught(self):
        skill_dir = self._create_valid_skill("python3-cmd")
        path = os.path.join(skill_dir, "references", "workflows.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Run `python3 -m unittest discover -s scripts`.\n")
        errors = validate_skill_dir(skill_dir, {"python3-cmd"})
        self.assertTrue(any("does not run from the repository root" in e for e in errors))

    def test_whole_repo_runner_is_allowed(self):
        skill_dir = self._create_valid_skill("repo-runner")
        path = os.path.join(skill_dir, "assets", "checklist.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("- [ ] Run `python tools/run_all_tests.py`.\n")
        self.assertEqual(validate_skill_dir(skill_dir, {"repo-runner"}), [])

    def test_verbose_flag_is_allowed(self):
        skill_dir = self._create_valid_skill("verbose-cmd")
        path = os.path.join(skill_dir, "references", "workflows.md")
        cmd = CANONICAL_TEST_CMD.format(name="verbose-cmd")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Run `{cmd} -v` for per-test output.\n")
        self.assertEqual(validate_skill_dir(skill_dir, {"verbose-cmd"}), [])

    def test_line_wrapped_command_is_allowed(self):
        """Markdown wraps long commands; the check compares on collapsed whitespace."""
        skill_dir = self._create_valid_skill("wrapped-cmd")
        path = os.path.join(skill_dir, "assets", "checklist.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("- [ ] Run\n      `python -m unittest discover -s\n"
                    "      skills/wrapped-cmd/scripts`.\n")
        self.assertEqual(validate_skill_dir(skill_dir, {"wrapped-cmd"}), [])


if __name__ == "__main__":
    unittest.main()
