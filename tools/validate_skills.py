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
    "When to Use", "Prerequisites", "Workflow",
    "Common Pitfalls", "Verification", "Related Skills",
]
REQUIRED_SUBDIRS = ["references", "scripts", "assets"]

ROOT = os.path.join(os.path.dirname(__file__), "..", "skills")


def validate_skill_dir(skill_dir, valid_skill_names):
    errors = []
    name = os.path.basename(skill_dir)

    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md_path):
        return [f"{name}: missing SKILL.md"]

    text = open(skill_md_path, encoding="utf-8").read()
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

    print("All skills passed structural, frontmatter, version, and cross-reference validation.")


if __name__ == "__main__":
    main()

