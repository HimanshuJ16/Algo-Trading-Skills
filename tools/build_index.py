#!/usr/bin/env python3
"""
Scans all directories under skills/, parses frontmatter from SKILL.md,
and builds an authoritative index.json database for the repository.
"""
import datetime
import glob
import json
import os
import sys

try:
    import yaml
except ImportError:
    print("This script requires pyyaml. Install with: pip install pyyaml")
    sys.exit(1)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT_DIR, "skills")
INDEX_PATH = os.path.join(ROOT_DIR, "index.json")


def build_index():
    skill_dirs = sorted(glob.glob(os.path.join(SKILLS_DIR, "*")))
    skills_metadata = []

    for s_dir in skill_dirs:
        if not os.path.isdir(s_dir):
            continue

        skill_name = os.path.basename(s_dir)
        skill_md = os.path.join(s_dir, "SKILL.md")

        if not os.path.isfile(skill_md):
            print(f"Warning: Missing SKILL.md in {skill_name}")
            continue

        content = open(skill_md, "r", encoding="utf-8").read()
        if not content.startswith("---"):
            print(f"Warning: No frontmatter in {skill_name}/SKILL.md")
            continue

        try:
            parts = content.split("---", 2)
            fm = yaml.safe_load(parts[1])
        except Exception as e:
            print(f"Error parsing frontmatter in {skill_name}: {e}")
            continue

        skill_entry = {
            "name": fm.get("name", skill_name),
            "description": fm.get("description", "").strip(),
            "domain": fm.get("domain", "algorithmic-trading"),
            "subdomain": fm.get("subdomain", ""),
            "tags": fm.get("tags", []),
            "brokers_frameworks": fm.get("brokers_frameworks", []),
            "version": str(fm.get("version", "1.0")),
            "author": fm.get("author", "algo-trading-skills-contributors"),
            "license": fm.get("license", "Apache-2.0"),
            "path": f"skills/{skill_name}",
            "skill_md": f"skills/{skill_name}/SKILL.md",
        }
        skills_metadata.append(skill_entry)

    index_data = {
        "version": "1.0.0",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "repository": "https://github.com/HimanshuJ16/Algo-Trading-Skills",
        "domain": "algorithmic-trading",
        "total_skills": len(skills_metadata),
        "skills": skills_metadata,
    }

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2)

    print(f"Successfully generated index.json with {len(skills_metadata)} skills.")


if __name__ == "__main__":
    build_index()
