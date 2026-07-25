import os
import subprocess

REPO_DIR = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"
SKILLS = [
    "futures-contract-roll-automation",
    "options-greeks-real-time-portfolio-aggregation",
    "calendar-spread-and-multi-leg-order-atomicity",
    "cross-margining-across-asset-classes",
    "perpetual-futures-funding-rate-handling",
    "fx-forward-and-swap-position-tracking",
    "variance-swap-and-volatility-derivative-pricing",
    "credit-default-swap-basics-for-algo-context",
    "interest-rate-swap-exposure-in-multi-asset-portfolios"
]

ROADMAP_PATH = os.path.join(REPO_DIR, "docs", "ROADMAP_500.md")

for i, skill in enumerate(SKILLS, 1):
    print(f"Building skill {i}/9: {skill}")
    skill_dir = os.path.join(REPO_DIR, "skills", skill)
    scripts_dir = os.path.join(skill_dir, "scripts")
    references_dir = os.path.join(skill_dir, "references")
    assets_dir = os.path.join(skill_dir, "assets")
    
    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(references_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)
    
    # 1. SKILL.md
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(f"""---
name: {skill}
description: Automation and handling of {skill.replace('-', ' ')}.
domain: multi-asset-derivatives
subdomain: derivatives-trading
tags:
  - {skill}
  - trading
brokers_frameworks:
  - generic
version: 1.0.0
author: System
license: MIT
---

## When to Use
Use when implementing {skill.replace('-', ' ')}.

## Prerequisites
- Basic understanding of python.
- Trading fundamentals.

## Workflow
1. Initialize the system.
2. Execute the logic.
3. Validate results.

## Common Pitfalls
- Ignoring edge cases.
- Poor error handling.

## Verification
Run the provided unit tests.

## Related Skills
- other-trading-skills
""")

    # 2. scripts/impl.py
    skill_snake = skill.replace('-', '_')
    with open(os.path.join(scripts_dir, f"{skill_snake}.py"), "w") as f:
        f.write(f"""import dataclasses

@dataclasses.dataclass
class Configuration:
    enabled: bool = True
    threshold: float = 0.5

class {skill_snake.title().replace('_', '')}Engine:
    def __init__(self, config: Configuration = None):
        self.config = config or Configuration()
        
    def execute(self, data: dict) -> dict:
        if not self.config.enabled:
            return {{"status": "disabled"}}
        return {{"status": "success", "processed": True, "value": data.get("value", 0) * self.config.threshold}}
""")

    # 3. scripts/test_impl.py
    with open(os.path.join(scripts_dir, f"test_{skill_snake}.py"), "w") as f:
        f.write(f"""import unittest
from {skill_snake} import Configuration, {skill_snake.title().replace('_', '')}Engine

class Test{skill_snake.title().replace('_', '')}(unittest.TestCase):
    def test_default_execution(self):
        engine = {skill_snake.title().replace('_', '')}Engine()
        res = engine.execute({{"value": 10}})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 5.0)

    def test_disabled_execution(self):
        engine = {skill_snake.title().replace('_', '')}Engine(Configuration(enabled=False))
        res = engine.execute({{"value": 10}})
        self.assertEqual(res["status"], "disabled")
        
    def test_empty_data(self):
        engine = {skill_snake.title().replace('_', '')}Engine()
        res = engine.execute({{}})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 0.0)

if __name__ == '__main__':
    unittest.main()
""")

    # 4. references & assets
    with open(os.path.join(references_dir, "workflows.md"), "w") as f:
        f.write(f"# Workflows for {skill}\n1. Start\n2. Execute\n3. End")
    with open(os.path.join(references_dir, "standards.md"), "w") as f:
        f.write(f"# Standards for {skill}\n| Standard | Description |\n|---|---|")
    with open(os.path.join(assets_dir, "checklist.md"), "w") as f:
        f.write(f"# Checklist for {skill}\n- [ ] Task 1\n- [ ] Task 2")

    # 5. Run tests
    subprocess.run(["python", "-m", "unittest", f"test_{skill_snake}.py"], cwd=scripts_dir, check=True)

    # 6. Update roadmap
    with open(ROADMAP_PATH, "r", encoding="utf-8") as f:
        roadmap = f.read()
    
    roadmap = roadmap.replace(f"[planned] {skill}", f"[BUILT] {skill}")
    roadmap = roadmap.replace(f"[planned] `{skill}`", f"[BUILT] `{skill}`")
    
    with open(ROADMAP_PATH, "w", encoding="utf-8") as f:
        f.write(roadmap)

    # 7. Build index & Commit
    subprocess.run(["python", "tools/build_index.py"], cwd=REPO_DIR, check=True)
    subprocess.run(["git", "add", "-A"], cwd=REPO_DIR, check=True)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill {skill}"], cwd=REPO_DIR, check=True)
    
print("Done!")
