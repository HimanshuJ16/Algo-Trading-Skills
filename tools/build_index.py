#!/usr/bin/env python3
"""
Builds index.json, the discovery database, from every skills/*/SKILL.md frontmatter.

    python tools/build_index.py            # rewrite index.json
    python tools/build_index.py --check    # exit 1 if index.json is stale (CI)
    python tools/build_index.py --output path/to/file.json

The index carries no timestamp, so regenerating it from unchanged sources produces no
diff. Its `version` is read from .claude-plugin/plugin.json. Frontmatter is parsed with
the same function the validator uses, and any skill that cannot be parsed fails the
build instead of silently dropping out of the index.
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_skills import parse_frontmatter, split_list  # noqa: E402

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT_DIR, "skills")
INDEX_PATH = os.path.join(ROOT_DIR, "index.json")
PLUGIN_PATH = os.path.join(ROOT_DIR, ".claude-plugin", "plugin.json")
REPOSITORY = "https://github.com/HimanshuJ16/Algo-Trading-Skills"


def repo_version():
    with open(PLUGIN_PATH, encoding="utf-8") as fh:
        return json.load(fh)["version"]


def build_index(skills_dir=SKILLS_DIR):
    """Return the index as a dict. Raises ValueError on any unparseable skill."""
    skills = []
    problems = []
    for s_dir in sorted(glob.glob(os.path.join(skills_dir, "*"))):
        if not os.path.isdir(s_dir):
            continue
        skill_name = os.path.basename(s_dir)
        skill_md = os.path.join(s_dir, "SKILL.md")
        if not os.path.isfile(skill_md):
            problems.append(f"{skill_name}: missing SKILL.md")
            continue
        with open(skill_md, encoding="utf-8") as fh:
            content = fh.read()
        try:
            fm, _ = parse_frontmatter(content)
        except ValueError as e:
            problems.append(f"{skill_name}: {e}")
            continue
        meta = fm.get("metadata") or {}
        if not isinstance(meta, dict):
            problems.append(f"{skill_name}: metadata is not a mapping")
            continue
        skills.append({
            "name": fm.get("name", skill_name),
            "description": " ".join(str(fm.get("description", "")).split()),
            "domain": str(meta.get("domain", "")),
            "subdomain": str(meta.get("subdomain", "")),
            "tags": split_list(meta.get("tags"), ","),
            "brokers_frameworks": split_list(meta.get("brokers_frameworks"), ";"),
            "version": str(meta.get("version", "")),
            "author": str(meta.get("author", "")),
            "license": str(fm.get("license", "")),
            "path": f"skills/{skill_name}",
            "skill_md": f"skills/{skill_name}/SKILL.md",
        })
    if problems:
        raise ValueError("; ".join(problems))

    by_subdomain = {}
    for s in skills:
        by_subdomain[s["subdomain"]] = by_subdomain.get(s["subdomain"], 0) + 1

    return {
        "version": repo_version(),
        "repository": REPOSITORY,
        "domain": "algorithmic-trading",
        "total_skills": len(skills),
        "subdomains": dict(sorted(by_subdomain.items())),
        "skills": skills,
    }


def render(index):
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if index.json is out of date")
    parser.add_argument("--output", default=INDEX_PATH, help="where to write (default: index.json)")
    args = parser.parse_args(argv)

    try:
        index = build_index()
    except ValueError as e:
        print(f"build_index: {e}")
        return 1
    text = render(index)

    if args.check:
        try:
            with open(args.output, encoding="utf-8") as fh:
                current = fh.read()
        except FileNotFoundError:
            current = ""
        if current != text:
            print(f"{os.path.relpath(args.output, ROOT_DIR)} is out of date -- run python tools/build_index.py")
            return 1
        print(f"index.json is up to date ({index['total_skills']} skills).")
        return 0

    with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"Successfully generated {os.path.relpath(args.output, ROOT_DIR)} with {index['total_skills']} skills.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
