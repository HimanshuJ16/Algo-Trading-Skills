#!/usr/bin/env python3
"""
Validates every skills/<name>/SKILL.md against the agentskills.io specification plus
the structure this repo adds on top of it:

  skills/<name>/
    SKILL.md                  (required; spec frontmatter + seven body sections)
    references/standards.md   (required)
    references/workflows.md   (required)
    scripts/<helper>.py       (required; at least one module not named test_*.py)
    scripts/test_<helper>.py  (required; at least one unittest suite)
    assets/checklist.md       (required)

Frontmatter follows the agentskills.io spec exactly: only `name`, `description`,
`license`, `compatibility`, `allowed-tools` and `metadata` may appear at the top
level, and everything this repo adds lives under `metadata:` as string values:

  ---
  name: order-placement-idempotency
  description: >-
    Use whenever a bot places, modifies, or cancels live orders ...
  license: Apache-2.0
  metadata:
    domain: algorithmic-trading
    subdomain: broker-integration            # one of SUBDOMAINS below
    tags: broker-integration, idempotency    # comma-separated
    brokers_frameworks: Fyers API v3; IBKR   # semicolon-separated (names may hold commas)
    version: "2.0.0"
    author: algo-trading-skills-contributors
  ---

Beyond the spec, this validator enforces:

  * `name` matches the directory, is kebab-case and at most 64 characters.
  * `description` starts with "Use " (a trigger, not a noun phrase) and is at most
    DESCRIPTION_MAX characters. The spec allows 1024, but every installed skill's
    description is loaded into the agent's context on every session, so the budget
    is deliberately tight.
  * `subdomain` is one of the 16 domains in docs/ROADMAP_500.md; one license and one
    author across the library; `version` is semver.
  * The seven body sections exist as level-2 headings.
  * Every backticked slug in `## Related Skills`, and every skill slug quoted in the
    repo-level docs (README, mappings/, docs/, examples/, llms*.txt), names a real
    skill directory.
  * scripts/ holds a helper module and a test_*.py suite, and no helper is itself
    named test_*.py (tools/run_all_tests.py collects test_*.py as suites).
  * Every test command quoted in a skill's Markdown is the repo-root form
    `python -m unittest discover -s skills/<name>/scripts`, and SKILL.md quotes it.
    A `cd skills/...` line ahead of that command is rejected too, because the
    repo-root path no longer resolves once the shell has changed directory.
  * No skill document claims a Python floor below PYTHON_FLOOR (3.10): several
    helpers use `dataclass(slots=True)` and `zoneinfo`, so a lower claim is false.
  * The version in .claude-plugin/plugin.json, .claude-plugin/marketplace.json and
    index.json agree, and every skill is listed in exactly one per-domain marketplace
    plugin entry.

Run locally with: python tools/validate_skills.py
Exits non-zero on any failure so CI fails the build.
"""
import glob
import json
import os
import re
import sys

try:
    import yaml
except ImportError:
    print("This script requires pyyaml. Install with: pip install pyyaml")
    sys.exit(1)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.join(REPO_ROOT, "skills")

# agentskills.io top-level fields.
ALLOWED_FRONTMATTER_FIELDS = {"name", "description", "license", "compatibility", "allowed-tools", "metadata"}
REQUIRED_FRONTMATTER_FIELDS = ["name", "description", "license", "metadata"]
# This repo's additions, all under metadata: as strings.
REQUIRED_METADATA_FIELDS = ["domain", "subdomain", "tags", "brokers_frameworks", "version", "author"]
ALLOWED_METADATA_FIELDS = set(REQUIRED_METADATA_FIELDS)

DOMAIN = "algorithmic-trading"
SUBDOMAINS = [
    "broker-integration", "real-time-architecture", "backtesting-methodology",
    "financial-ml", "risk-management", "deployment-ops", "global-market-integration",
    "regulatory-compliance-global", "multi-asset-derivatives", "execution-algorithms",
    "data-management-global", "crypto-custody-security", "portfolio-multi-strategy",
    "market-microstructure-latency", "quant-research-alt-data",
    "tax-accounting-reporting-global",
]
LICENSE = "Apache-2.0"
AUTHOR = "algo-trading-skills-contributors"

NAME_MAX = 64
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DESCRIPTION_MAX = 280
DESCRIPTION_TRIGGER_RE = re.compile(r"^Use\b")
VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")
MARKETING_WORDS = ("institutional", "production-grade", "enterprise-grade")

REQUIRED_SECTIONS = [
    "When to Use", "When NOT to Use", "Prerequisites", "Workflow",
    "Common Pitfalls", "Verification", "Related Skills",
]
REQUIRED_SUBDIRS = ["references", "scripts", "assets"]
REQUIRED_FILES = ["references/standards.md", "references/workflows.md", "assets/checklist.md"]

# The only test invocation a skill's Markdown may quote, besides the whole-repo runner.
CANONICAL_TEST_CMD = "python -m unittest discover -s skills/{name}/scripts"
WHOLE_REPO_TEST_CMD = "python tools/run_all_tests.py"
# Matches a Python test invocation of any shape, so non-canonical ones can be reported.
TEST_CMD_RE = re.compile(
    r"(?:cd\s+[^`\n]+?&&\s*)?"
    r"(?:python3?|pytest)\s+(?:-m\s+(?:unittest|pytest)[^`\n]*|[^`\n]*?test_[A-Za-z0-9_]+\.py[^`\n]*)"
)
# Markdown soft-wraps a long command onto an indented continuation line. Rejoin those
# before scanning so a wrapped command is read as the single command it is.
SOFT_WRAP_RE = re.compile(r"\n[ \t]+")
# A `cd skills/<name>/scripts` line on its own breaks the repo-root command that follows it.
CD_INTO_SKILL_RE = re.compile(r"^\s*cd\s+skills/\S+", re.M)
# The library's interpreter floor; a skill may not advertise a lower one.
PYTHON_FLOOR = (3, 10)
PYTHON_CLAIM_RE = re.compile(r"Python\s+(\d+)\.(\d+)\+")

# Repo-level documents whose backticked skill slugs must resolve to real skills.
REPO_DOCS = [
    "README.md", "CLAUDE.md", "AGENTS.md", "llms.txt", "llms-full.txt",
    "docs/architecture.md", "docs/skill-anatomy.md", "docs/ROADMAP_500.md",
    "mappings/broker-api-coverage.md", "mappings/regulatory-coverage.md",
    "examples/README.md",
]
# Backticked kebab-case identifiers in those docs that are not skill slugs: the plugin
# names the marketplace generates, plus a few repo-specific terms.
NON_SKILL_SLUGS = set(SUBDOMAINS) | {f"algo-trading-{d}" for d in SUBDOMAINS} | {
    "algo-trading-skills", "algo-trading-skills-all", "algo-trading-skills-contributors",
    "agentskills-io", "requirements-dev", "run-all-tests", "validate-skills",
    "build-index", "build-marketplace", "sub-second",
}


def parse_frontmatter(text):
    """Split SKILL.md text into (frontmatter dict, body). Raises ValueError."""
    if text.startswith("﻿"):
        text = text[1:]
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", text, re.S)
    if not m:
        raise ValueError("SKILL.md missing YAML frontmatter block")
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML frontmatter ({e})")
    if not isinstance(fm, dict):
        raise ValueError("frontmatter is not a YAML mapping")
    return fm, m.group(2)


def split_list(value, sep):
    return [v.strip() for v in str(value or "").split(sep) if v.strip()]


def validate_frontmatter(name, fm):
    errors = []
    unknown = set(fm) - ALLOWED_FRONTMATTER_FIELDS
    if unknown:
        errors.append(f"{name}: unexpected top-level frontmatter field(s) {sorted(unknown)} "
                      f"-- the agentskills.io spec allows only {sorted(ALLOWED_FRONTMATTER_FIELDS)}; "
                      f"put repo fields under metadata:")
    for field in REQUIRED_FRONTMATTER_FIELDS:
        if field not in fm:
            errors.append(f"{name}: frontmatter missing required field '{field}'")

    fm_name = fm.get("name")
    if fm_name != name:
        errors.append(f"{name}: frontmatter 'name' ({fm_name!r}) does not match directory name ({name!r})")
    if not isinstance(name, str) or not NAME_RE.match(name) or len(name) > NAME_MAX:
        errors.append(f"{name}: name must be kebab-case (a-z, 0-9, single hyphens) and at most {NAME_MAX} chars")

    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        errors.append(f"{name}: description must be a non-empty string")
    else:
        flat = " ".join(desc.split())
        if len(flat) > DESCRIPTION_MAX:
            errors.append(f"{name}: description is {len(flat)} chars; maximum is {DESCRIPTION_MAX} "
                          f"(every installed skill's description is loaded into the agent's context)")
        if not DESCRIPTION_TRIGGER_RE.match(flat):
            errors.append(f"{name}: description must start with 'Use when ...' (a trigger condition), "
                          f"not a noun phrase: {flat[:60]!r}")
        lowered = flat.lower()
        for w in MARKETING_WORDS:
            if w in lowered:
                errors.append(f"{name}: description contains marketing word {w!r}")

    if fm.get("license") != LICENSE:
        errors.append(f"{name}: license must be {LICENSE!r} (matches the repository LICENSE), got {fm.get('license')!r}")

    meta = fm.get("metadata")
    if not isinstance(meta, dict):
        errors.append(f"{name}: frontmatter 'metadata' must be a mapping")
        return errors
    unknown_meta = set(meta) - ALLOWED_METADATA_FIELDS
    if unknown_meta:
        errors.append(f"{name}: unexpected metadata field(s) {sorted(unknown_meta)}")
    for field in REQUIRED_METADATA_FIELDS:
        if field not in meta:
            errors.append(f"{name}: metadata missing required field '{field}'")
    for key, value in meta.items():
        if not isinstance(value, str):
            errors.append(f"{name}: metadata.{key} must be a string (spec: metadata maps strings to strings), "
                          f"got {type(value).__name__}")
    if str(meta.get("domain")) != DOMAIN:
        errors.append(f"{name}: metadata.domain must be {DOMAIN!r}")
    if str(meta.get("subdomain")) not in SUBDOMAINS:
        errors.append(f"{name}: metadata.subdomain {meta.get('subdomain')!r} is not one of the 16 domains "
                      f"in docs/ROADMAP_500.md")
    if not split_list(meta.get("tags"), ","):
        errors.append(f"{name}: metadata.tags must list at least one comma-separated tag")
    version = str(meta.get("version", "")).strip()
    if not VERSION_RE.match(version):
        errors.append(f"{name}: metadata.version {version!r} is not semver (e.g. \"1.0.0\"); quote it")
    if str(meta.get("author")) != AUTHOR:
        errors.append(f"{name}: metadata.author must be {AUTHOR!r}")
    return errors


def validate_skill_dir(skill_dir, valid_skill_names):
    errors = []
    name = os.path.basename(skill_dir)

    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md_path):
        return [f"{name}: missing SKILL.md"]

    with open(skill_md_path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        frontmatter, body = parse_frontmatter(text)
    except ValueError as e:
        return [f"{name}: {e}"]

    errors.extend(validate_frontmatter(name, frontmatter))

    for section in REQUIRED_SECTIONS:
        if not re.search(rf"^## {re.escape(section)}\s*$", body, re.M):
            errors.append(f"{name}: missing required section '## {section}' (exact level-2 heading)")

    # Validate Related Skills cross-references
    rel = re.search(r"^## Related Skills\s*$(.*?)(?=^## |\Z)", body, re.M | re.S)
    if rel:
        refs = re.findall(r"`([a-z0-9][a-z0-9-]+)`", rel.group(1))
        for r in refs:
            if r != name and r not in valid_skill_names and len(r) > 3 and "-" in r:
                errors.append(f"{name}: broken Related Skills reference `{r}`")

    for subdir in REQUIRED_SUBDIRS:
        subdir_path = os.path.join(skill_dir, subdir)
        if not os.path.isdir(subdir_path):
            errors.append(f"{name}: missing required subdirectory '{subdir}/'")
        elif not [f for f in os.listdir(subdir_path) if not f.startswith((".", "__"))]:
            errors.append(f"{name}: '{subdir}/' exists but is empty")
    for rel_file in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(skill_dir, rel_file)):
            errors.append(f"{name}: missing required file '{rel_file}'")

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
    if canonical not in SOFT_WRAP_RE.sub(" ", skill_md_text):
        errors.append(
            f"{name}: SKILL.md never shows how to run the tests -- its Verification "
            f"section should quote `{canonical}`")

    docs = ([os.path.join(skill_dir, "SKILL.md")]
            + sorted(glob.glob(os.path.join(skill_dir, "references", "*.md")))
            + sorted(glob.glob(os.path.join(skill_dir, "assets", "*.md"))))
    for doc in docs:
        with open(doc, encoding="utf-8") as fh:
            raw = fh.read()
        rel = os.path.relpath(doc, os.path.dirname(skill_dir)).replace(os.sep, "/")
        for found in CD_INTO_SKILL_RE.findall(raw):
            errors.append(
                f"{name}: {rel} documents `{found.strip()}` -- the test command runs from "
                f"the repository root, so drop the cd")
        for major, minor in PYTHON_CLAIM_RE.findall(raw):
            if (int(major), int(minor)) < PYTHON_FLOOR:
                errors.append(
                    f"{name}: {rel} claims Python {major}.{minor}+, below the library "
                    f"floor of {PYTHON_FLOOR[0]}.{PYTHON_FLOOR[1]}")
        text = SOFT_WRAP_RE.sub(" ", raw)
        for found in TEST_CMD_RE.findall(text):
            cmd = " ".join(found.split())
            if cmd.endswith(" -v"):
                cmd = cmd[:-len(" -v")]
            if cmd in (canonical, WHOLE_REPO_TEST_CMD):
                continue
            errors.append(
                f"{name}: {rel} documents `{cmd}`, which does not run from the "
                f"repository root -- use `{canonical}`")
    return errors


def validate_repo_docs(valid_skill_names):
    """Backticked skill slugs in the repo-level docs must name real skills."""
    errors = []
    for rel in REPO_DOCS:
        path = os.path.join(REPO_ROOT, rel)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for slug in sorted(set(re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+){2,})`", text))):
            if slug in valid_skill_names or slug in NON_SKILL_SLUGS:
                continue
            errors.append(f"{rel}: references `{slug}`, which is not a skill directory")
    for py in sorted(glob.glob(os.path.join(REPO_ROOT, "examples", "*.py"))):
        with open(py, encoding="utf-8") as fh:
            text = fh.read()
        for slug in sorted(set(re.findall(r"skills[/\\]([a-z0-9-]+)[/\\]scripts", text))):
            if slug not in valid_skill_names:
                errors.append(f"examples/{os.path.basename(py)}: imports from skills/{slug}/scripts, "
                              f"which does not exist")
    return errors


def load_json(rel):
    path = os.path.join(REPO_ROOT, rel)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate_packaging(valid_skill_names):
    """Plugin manifests and index.json agree on version and cover every skill once."""
    errors = []
    plugin = load_json(".claude-plugin/plugin.json")
    market = load_json(".claude-plugin/marketplace.json")
    index = load_json("index.json")
    if not plugin or not market:
        return ["missing .claude-plugin/plugin.json or marketplace.json"]
    version = plugin.get("version")
    if market.get("metadata", {}).get("version") != version:
        errors.append(f"marketplace.json metadata.version != plugin.json version ({version})")
    if index is not None and index.get("version") != version:
        errors.append(f"index.json version {index.get('version')!r} != plugin.json version {version!r}; "
                      f"run python tools/build_index.py")

    seen = {}
    for entry in market.get("plugins", []):
        if entry.get("version") != version:
            errors.append(f"marketplace.json plugin {entry.get('name')!r} version != {version}")
        paths = entry.get("skills")
        if not paths or paths == ["./skills/"] or paths == "./skills/":
            continue  # the all-skills entry keeps the full scan
        for p in paths:
            m = re.match(r"^\./skills/([a-z0-9-]+)/?$", p)
            if not m or m.group(1) not in valid_skill_names:
                errors.append(f"marketplace.json plugin {entry.get('name')!r} lists {p!r}, not a skill directory")
                continue
            seen.setdefault(m.group(1), []).append(entry.get("name"))
    if seen:
        for slug in sorted(valid_skill_names - set(seen)):
            errors.append(f"marketplace.json: skill {slug!r} is in no per-domain plugin; "
                          f"run python tools/build_marketplace.py")
        for slug, owners in sorted(seen.items()):
            if len(owners) > 1:
                errors.append(f"marketplace.json: skill {slug!r} appears in several plugins {owners}")
    return errors


def main():
    skill_dirs = sorted(
        d for d in glob.glob(os.path.join(ROOT, "*")) if os.path.isdir(d)
    )
    if not skill_dirs:
        print("No skill directories found under skills/ -- check ROOT path.")
        sys.exit(1)

    valid_skill_names = set(os.path.basename(d) for d in skill_dirs)
    lowered = {}
    all_errors = []
    for skill_dir in skill_dirs:
        n = os.path.basename(skill_dir)
        if n.lower() in lowered:
            all_errors.append(f"{n}: collides case-insensitively with {lowered[n.lower()]}")
        lowered[n.lower()] = n
        all_errors.extend(validate_skill_dir(skill_dir, valid_skill_names))
    all_errors.extend(validate_repo_docs(valid_skill_names))
    all_errors.extend(validate_packaging(valid_skill_names))

    print(f"Validated {len(skill_dirs)} skills.")
    if all_errors:
        print(f"\n{len(all_errors)} problem(s) found:\n")
        for e in all_errors:
            print(f"  - {e}")
        sys.exit(1)

    print("All skills passed structural, frontmatter, version, cross-reference, "
          "scripts-layout, test-command, and packaging validation.")


if __name__ == "__main__":
    main()
