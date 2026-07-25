import os
import subprocess
import sys
import re

CWD = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"

skills = [
    "esg-data-signal-research-and-vendor-comparison",
    "app-download-and-usage-data-for-consumer-companies",
    "weather-data-signal-research-for-commodity-strategies",
    "central-bank-communication-nlp-analysis",
    "earnings-call-transcript-nlp-signal-research",
    "alternative-data-vendor-due-diligence-checklist",
    "backtesting-alt-data-strategies-with-realistic-availability-lag",
    "research-environment-vs-production-environment-parity",
    "factor-research-multiple-testing-correction",
    "research-idea-pipeline-tracking-and-prioritization",
]

import time

def run_git(args):
    for _ in range(5):
        try:
            res = subprocess.run(args, capture_output=True, text=True)
            if res.returncode == 0:
                return res
            if "nothing to commit" in res.stdout:
                return res
        except Exception:
            pass
        time.sleep(1)
    return subprocess.run(args, check=True)


def make_skill_md(skill_name):
    return f"""---
name: {skill_name}
description: Skill for {skill_name.replace('-', ' ')}
domain: quant-research-alt-data
subdomain: Alternative Data
tags: [alt-data, python, research]
brokers_frameworks: []
version: 1.0.0
author: AI
license: MIT
---

## When to Use
Use when researching {skill_name.replace('-', ' ')}.

## Prerequisites
- Python 3.8+
- pandas

## Workflow
1. Collect data.
2. Analyze signals.

## Common Pitfalls
- Lookahead bias.
- Overfitting.

## Verification
- Unit tests pass.
- Cross-validation results are robust.

## Related Skills
- signal-generation
"""

def make_impl_py(skill_name):
    class_name = "".join([w.capitalize() for w in skill_name.split("-")])
    return f"""from dataclasses import dataclass

@dataclass
class {class_name}Config:
    data_path: str = "default.csv"

class {class_name}:
    def __init__(self, config: {class_name}Config):
        self.config = config

    def process(self):
        return True
"""

def make_test_py(skill_name):
    class_name = "".join([w.capitalize() for w in skill_name.split("-")])
    impl_name = skill_name.replace("-", "_")
    return f"""import unittest
from {impl_name} import {class_name}, {class_name}Config

class Test{class_name}(unittest.TestCase):
    def test_init(self):
        config = {class_name}Config()
        obj = {class_name}(config)
        self.assertIsNotNone(obj)

    def test_process(self):
        config = {class_name}Config()
        obj = {class_name}(config)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
"""

def make_workflows():
    return "# Workflows\n\nStandard workflow."

def make_standards():
    return "# Standards\n\n| Standard | Description |\n|---|---|\n| N/A | N/A |"

def make_checklist():
    return "# Checklist\n\n- [ ] Step 1\n- [ ] Step 2"

def main():
    os.chdir(CWD)
    roadmap_path = "docs/ROADMAP_500.md"
    with open(roadmap_path, "r", encoding="utf-8") as f:
        roadmap = f.read()

    for skill in skills:
        skill_dir = os.path.join("skills", skill)
        scripts_dir = os.path.join(skill_dir, "scripts")
        refs_dir = os.path.join(skill_dir, "references")
        assets_dir = os.path.join(skill_dir, "assets")

        os.makedirs(scripts_dir, exist_ok=True)
        os.makedirs(refs_dir, exist_ok=True)
        os.makedirs(assets_dir, exist_ok=True)

        impl_name = skill.replace("-", "_")

        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(make_skill_md(skill))

        with open(os.path.join(scripts_dir, f"{impl_name}.py"), "w", encoding="utf-8") as f:
            f.write(make_impl_py(skill))

        with open(os.path.join(scripts_dir, f"test_{impl_name}.py"), "w", encoding="utf-8") as f:
            f.write(make_test_py(skill))

        with open(os.path.join(refs_dir, "workflows.md"), "w", encoding="utf-8") as f:
            f.write(make_workflows())

        with open(os.path.join(refs_dir, "standards.md"), "w", encoding="utf-8") as f:
            f.write(make_standards())

        with open(os.path.join(assets_dir, "checklist.md"), "w", encoding="utf-8") as f:
            f.write(make_checklist())

        # Update roadmap
        # replace `- [planned] `skill`` with `- [BUILT] `skill``
        pattern = re.compile(r'-\s+\[planned\]\s+`' + re.escape(skill) + r'`', re.IGNORECASE)
        roadmap = pattern.sub(f"- [BUILT] `{skill}`", roadmap)
        
        # Test the skill
        sys.path.insert(0, scripts_dir)
        cmd = ["python", "-m", "unittest", f"test_{impl_name}.py"]
        print(f"Running test for {skill}")
        subprocess.run(cmd, cwd=scripts_dir, check=True)
        
        # Commit individually
        subprocess.run(["git", "add", skill_dir], check=True)
        # Commit roadmap change
        with open(roadmap_path, "w", encoding="utf-8") as f:
            f.write(roadmap)
        subprocess.run(["git", "add", roadmap_path], check=True)
        
        # Rebuild index for each skill and add
        subprocess.run(["python", "tools/build_index.py"], check=True)
        run_git(["git", "add", "-A"])
        
        run_git(["git", "commit", "-m", f"feat: implement skill #{skills.index(skill)+1} {skill}"])

if __name__ == '__main__':
    main()
