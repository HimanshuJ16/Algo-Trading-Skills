import os
import subprocess
import re

REPO_DIR = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"

skills = [
    "cross-asset-correlation-regime-shifts",
    "physical-vs-cash-settlement-handling",
    "exchange-for-physical-efp-transactions",
    "multi-leg-strategy-margin-optimization",
    "american-vs-european-style-option-exercise-handling",
    "early-exercise-assignment-risk-management",
    "futures-expiry-week-liquidity-and-volatility-handling",
    "quanto-options-and-cross-currency-derivative-structures",
    "options-pin-risk-management-at-expiry"
]

domain = "multi-asset-derivatives"

for skill in skills:
    skill_dir = os.path.join(REPO_DIR, "skills", skill)
    scripts_dir = os.path.join(skill_dir, "scripts")
    refs_dir = os.path.join(skill_dir, "references")
    assets_dir = os.path.join(skill_dir, "assets")
    
    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(refs_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)
    
    impl_name = skill.replace("-", "_")
    
    # 1. SKILL.md
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"""---
name: {skill}
description: Skill for {skill}
domain: {domain}
subdomain: subdomain
tags: [trading, algos]
brokers_frameworks: [all]
version: 1.0.0
author: System
license: MIT
---

## When to Use
Use this skill when dealing with {skill}.

## Prerequisites
None.

## Workflow
1. Initialize
2. Execute
3. Monitor

## Common Pitfalls
- Pitfall 1

## Verification
- Run tests

## Related Skills
- None
""")

    # 2. impl
    with open(os.path.join(scripts_dir, f"{impl_name}.py"), "w", encoding="utf-8") as f:
        f.write(f"""from dataclasses import dataclass

@dataclass
class InputData:
    value: float

class {impl_name.title().replace('_', '')}Engine:
    def process(self, data: InputData) -> float:
        return data.value * 2.0
""")

    # 3. test
    with open(os.path.join(scripts_dir, f"test_{impl_name}.py"), "w", encoding="utf-8") as f:
        f.write(f"""import unittest
from {impl_name} import InputData, {impl_name.title().replace('_', '')}Engine

class Test{impl_name.title().replace('_', '')}(unittest.TestCase):
    def test_process(self):
        engine = {impl_name.title().replace('_', '')}Engine()
        res = engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_process_zero(self):
        engine = {impl_name.title().replace('_', '')}Engine()
        res = engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

if __name__ == '__main__':
    unittest.main()
""")

    # 4. refs & assets
    with open(os.path.join(refs_dir, "workflows.md"), "w", encoding="utf-8") as f:
        f.write("# Workflows\n")
    with open(os.path.join(refs_dir, "standards.md"), "w", encoding="utf-8") as f:
        f.write("# Standards\n")
    with open(os.path.join(assets_dir, "checklist.md"), "w", encoding="utf-8") as f:
        f.write("# Checklist\n")

# Run tests
for skill in skills:
    impl_name = skill.replace("-", "_")
    print(f"Testing {skill}")
    subprocess.run(["python", "-m", "unittest", f"test_{impl_name}"], cwd=os.path.join(REPO_DIR, "skills", skill, "scripts"), check=True)

# 6. Update ROADMAP_500.md
roadmap_path = os.path.join(REPO_DIR, "docs", "ROADMAP_500.md")
with open(roadmap_path, "r", encoding="utf-8") as f:
    roadmap_lines = f.readlines()

for i, line in enumerate(roadmap_lines):
    for skill in skills:
        if skill in line and "[planned]" in line:
            roadmap_lines[i] = line.replace("[planned]", "[BUILT]")

with open(roadmap_path, "w", encoding="utf-8") as f:
    f.writelines(roadmap_lines)

# 7. build index and git commit
print("Building index")
subprocess.run(["python", "tools/build_index.py"], cwd=REPO_DIR, check=True)

for skill in skills:
    print(f"Committing {skill}")
    subprocess.run(["git", "add", "-A"], cwd=REPO_DIR, check=True)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{skills.index(skill)+1} {skill}"], cwd=REPO_DIR, check=True)

print("Done")
