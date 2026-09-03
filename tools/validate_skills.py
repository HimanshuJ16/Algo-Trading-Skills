#!/usr/bin/env python3
"""
Validates every skills/<name>/SKILL.md against the agentskills.io-derived
structure this repo follows:

  skills/<name>/
    SKILL.md            (required)
    references/         (required directory, at least one file)
    scripts/             (required directory, at least one file)
    assets/              (required directory, at least one file)

and checks SKILL.md frontmatter for required fields and required body sections.

Two further conventions keep the library runnable:

  * scripts/ holds at least one helper module and at least one test_*.py suite, and
    no helper is itself named test_*.py. tools/run_all_tests.py discovers suites by
    globbing test_*.py, so a helper matching that glob is collected as if it were a
    suite and inflates the reported file count.
  * every test command quoted in a skill's Markdown is the repo-root form
    `python -m unittest discover -s skills/<name>/scripts`, and SKILL.md quotes it at
    least once. Relative forms such as `python scripts/test_x.py` fail outright for a
    reader who runs them from the repository root, which is where every other
    documented command in this repo runs.

Run locally with: python tools/validate_skills.py
Exits non-zero on any failure so CI fails the build.
"""
import glob
import os
import re
import sys

try:
    import yaml
except ImportError:
    print("This script requires pyyaml. Install with: pip install pyyaml")
    sys.exit(1)

REQUIRED_FRONTMATTER_FIELDS = [
    "name", "description", "domain", "subdomain", "tags",
    "brokers_frameworks", "version", "author", "license",
]
REQUIRED_SECTIONS = [
    "When to Use", "When NOT to Use", "Prerequisites", "Workflow",
    "Common Pitfalls", "Verification", "Related Skills",
]
REQUIRED_SUBDIRS = ["references", "scripts", "assets"]

ROOT = os.path.join(os.path.dirname(__file__), "..", "skills")

# The only test invocation a skill's Markdown may quote, besides the whole-repo runner.
CANONICAL_TEST_CMD = "python -m unittest discover -s skills/{name}/scripts"
WHOLE_REPO_TEST_CMD = "python tools/run_all_tests.py"
# Matches a Python test invocation of any shape, so non-canonical ones can be reported.
TEST_CMD_RE = re.compile(
    r"(?:cd\s+[^`\n]+?&&\s*)?"
    r"python\s+(?:-m\s+unittest[^`\n]*|[^`\n]*?test_[A-Za-z0-9_]+\.py[^`\n]*)"
)
# Markdown soft-wraps a long command onto an indented continuation line. Rejoin those
# before scanning so a wrapped command is read as the single command it is. Only an
# indented continuation is joined, so unrelated paragraphs are never run together.
SOFT_WRAP_RE = re.compile(r"\n[ \t]+")


def validate_skill_dir(skill_dir, valid_skill_names):
    errors = []
    name = os.path.basename(skill_dir)

    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md_path):
        return [f"{name}: missing SKILL.md"]

    with open(skill_md_path, encoding="utf-8") as fh:
        text = fh.read()
    fm_match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not fm_match:
        return [f"{name}: SKILL.md missing YAML frontmatter block"]

    try:
        frontmatter = yaml.safe_load(fm_match.group(1))
    except yaml.YAMLError as e:
        return [f"{name}: invalid YAML frontmatter ({e})"]

    for field in REQUIRED_FRONTMATTER_FIELDS:
        if field not in frontmatter:
            errors.append(f"{name}: frontmatter missing required field '{field}'")

    if frontmatter.get("name") != name:
        errors.append(
            f"{name}: frontmatter 'name' ({frontmatter.get('name')!r}) "
            f"does not match directory name ({name!r})")

    # Validate version string format
    version_val = str(frontmatter.get("version", "")).strip()
    if not re.match(r"^\d+\.\d+(\.\d+)?$", version_val):
        errors.append(f"{name}: version '{version_val}' is not a valid semver string (e.g. '1.0.0')")

    body = fm_match.group(2)
    for section in REQUIRED_SECTIONS:
        if f"## {section}" not in body:
            errors.append(f"{name}: missing required section '## {section}'")

    # Validate Related Skills cross-references
    if "## Related Skills" in body:
        rel_section = body.split("## Related Skills")[1].split("\n## ")[0]
        refs = re.findall(r"`([a-z0-9\-]+)`", rel_section)
        for r in refs:
            if r != name and r not in valid_skill_names and len(r) > 3:
                errors.append(f"{name}: broken Related Skills reference `{r}`")

    for subdir in REQUIRED_SUBDIRS:
        subdir_path = os.path.join(skill_dir, subdir)
        if not os.path.isdir(subdir_path):
            errors.append(f"{name}: missing required subdirectory '{subdir}/'")
        elif not os.listdir(subdir_path):
            errors.append(f"{name}: '{subdir}/' exists but is empty")

    errors.extend(validate_scripts_layout(skill_dir, name))
    errors.extend(validate_test_commands(skill_dir, name, text))
    return errors


def validate_scripts_layout(skill_dir, name):
    """scripts/ needs a helper module and a test suite, told apart by filename."""
    errors = []
    modules = sorted(os.path.basename(f)
                     for f in glob.glob(os.path.join(skill_dir, "scripts", "*.py")))
    helpers = [m for m in modules if not m.startswith("test_")]
    suites = [m for m in modules if m.startswith("test_")]
    if not helpers:
        listed = ", ".join(modules) if modules else "no .py files"
        errors.append(
            f"{name}: scripts/ has no helper module -- every .py there is named "
            f"test_*.py, so run_all_tests.py collects the helper as a test suite. "
            f"Rename it after what it implements ({listed})")
    if not suites:
        errors.append(f"{name}: scripts/ has no test_*.py suite")
    return errors


def validate_test_commands(skill_dir, name, skill_md_text):
    """Every test command quoted in the skill's Markdown must run from the repo root."""
    errors = []
    canonical = CANONICAL_TEST_CMD.format(name=name)
    if canonical not in skill_md_text:
        errors.append(
            f"{name}: SKILL.md never shows how to run the tests -- its Verification "
            f"section should quote `{canonical}`")

    docs = ([os.path.join(skill_dir, "SKILL.md")]
            + sorted(glob.glob(os.path.join(skill_dir, "references", "*.md")))
            + sorted(glob.glob(os.path.join(skill_dir, "assets", "*.md"))))
    for doc in docs:
        with open(doc, encoding="utf-8") as fh:
            text = SOFT_WRAP_RE.sub(" ", fh.read())
        for found in TEST_CMD_RE.findall(text):
            # Markdown wraps long commands, so compare on collapsed whitespace.
            cmd = " ".join(found.split())
            if cmd.endswith(" -v"):
                cmd = cmd[:-len(" -v")]
            if cmd in (canonical, WHOLE_REPO_TEST_CMD):
                continue
            rel = os.path.relpath(doc, os.path.dirname(skill_dir)).replace(os.sep, "/")
            errors.append(
                f"{name}: {rel} documents `{cmd}`, which does not run from the "
                f"repository root -- use `{canonical}`")
    return errors


def main():
    skill_dirs = sorted(
        d for d in glob.glob(os.path.join(ROOT, "*")) if os.path.isdir(d)
    )
    if not skill_dirs:
        print("No skill directories found under skills/ -- check ROOT path.")
        sys.exit(1)

    valid_skill_names = set(os.path.basename(d) for d in skill_dirs)
    all_errors = []
    for skill_dir in skill_dirs:
        all_errors.extend(validate_skill_dir(skill_dir, valid_skill_names))

    print(f"Validated {len(skill_dirs)} skills.")
    if all_errors:
        print(f"\n{len(all_errors)} problem(s) found:\n")
        for e in all_errors:
            print(f"  - {e}")
        sys.exit(1)

    print("All skills passed structural, frontmatter, version, cross-reference, "
          "scripts-layout, and test-command validation.")


if __name__ == "__main__":
    main()
