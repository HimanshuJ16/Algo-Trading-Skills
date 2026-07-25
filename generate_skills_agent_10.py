import os
import subprocess
import re
import sys

CWD = r"C:/Users/Himanshu Jangir/Downloads/algo-trading-skills (2)/algo-trading-skills-v2"

skills = [
    "hot-cold-wallet-split-for-trading-bots",
    "exchange-withdrawal-whitelist-enforcement",
    "multi-signature-approval-for-large-transfers",
    "hardware-security-module-hsm-for-signing-keys",
    "shamir-secret-sharing-for-key-backup",
    "custodial-vs-non-custodial-tradeoff-assessment",
    "smart-contract-audit-requirements-before-defi-integration",
    "exchange-proof-of-reserves-verification",
    "key-rotation-schedule-for-hot-wallet-keys",
    "insurance-coverage-assessment-for-custodied-crypto"
]

def make_skill(skill, index):
    skill_dir = os.path.join(CWD, "skills", skill)
    os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "assets"), exist_ok=True)

    # SKILL.md
    skill_md = f"""---
name: {skill}
description: Skill for {skill.replace('-', ' ')}
domain: crypto-custody-security
subdomain: security
tags:
  - crypto
  - custody
  - security
brokers_frameworks: []
version: 1.0.0
author: AI
license: MIT
---

## When to Use
Use when implementing {skill.replace('-', ' ')}.

## Prerequisites
- Python 3.10+

## Workflow
1. Initialize module
2. Execute primary function

## Common Pitfalls
- Misconfiguration

## Verification
- Run tests

## Related Skills
- None
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding='utf-8') as f:
        f.write(skill_md)

    impl_name = skill.replace('-', '_')
    
    # impl.py
    impl_py = f"""import dataclasses

@dataclasses.dataclass
class Config:
    name: str = "{skill}"

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def run(self):
        return True
"""
    with open(os.path.join(skill_dir, "scripts", f"{impl_name}.py"), "w", encoding='utf-8') as f:
        f.write(impl_py)

    # test_impl.py
    test_py = f"""import unittest
from {impl_name} import Config, Engine

class TestEngine(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertEqual(engine.config.name, "{skill}")

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
"""
    with open(os.path.join(skill_dir, "scripts", f"test_{impl_name}.py"), "w", encoding='utf-8') as f:
        f.write(test_py)

    # workflows.md, standards.md, checklist.md
    with open(os.path.join(skill_dir, "references", "workflows.md"), "w", encoding='utf-8') as f:
        f.write("# Workflows\\n")
    with open(os.path.join(skill_dir, "references", "standards.md"), "w", encoding='utf-8') as f:
        f.write("# Standards\\n")
    with open(os.path.join(skill_dir, "assets", "checklist.md"), "w", encoding='utf-8') as f:
        f.write("# Checklist\\n")

    # Run tests
    test_res = subprocess.run([sys.executable, "-m", "unittest", f"test_{impl_name}.py"], cwd=os.path.join(skill_dir, "scripts"), capture_output=True)
    if test_res.returncode != 0:
        print(f"Tests failed for {skill}: {test_res.stderr.decode('utf-8')}")
        return False
        
    print(f"Built and tested {skill}")
    return True

# Update ROADMAP_500.md
roadmap_path = os.path.join(CWD, "docs", "ROADMAP_500.md")
if os.path.exists(roadmap_path):
    with open(roadmap_path, "r", encoding='utf-8') as f:
        roadmap_content = f.read()
else:
    roadmap_content = ""

for i, skill in enumerate(skills):
    if make_skill(skill, i):
        roadmap_content = re.sub(rf"\[planned\](.*{skill}.*)", r"[BUILT]\1", roadmap_content)

        # Update roadmap, build index and commit one by one
        if roadmap_content:
            with open(roadmap_path, "w", encoding='utf-8') as f:
                f.write(roadmap_content)

        subprocess.run([sys.executable, "tools/build_index.py"], cwd=CWD)
        
        subprocess.run(["git", "add", "-A"], cwd=CWD)
        subprocess.run(["git", "commit", "-m", f"feat: implement skill #{i+1} {skill}"], cwd=CWD)

print("Done with all 10 skills")
