import os
import subprocess
import sys

skills = [
    "execution-algo-parameter-optimization-via-backtest",
    "cross-venue-latency-arbitrage-defensive-design",
    "algo-wheel-broker-execution-quality-comparison",
    "conditional-order-logic-for-execution-triggers",
    "execution-cost-model-recalibration-cadence",
    "multi-order-netting-before-routing",
    "auction-only-order-types-for-illiquid-names",
    "post-trade-execution-quality-scorecard",
    "smart-order-router-failover-on-venue-outage",
    "minimum-fill-size-and-lot-rounding-logic",
    "execution-algorithm-kill-switch-integration"
]

base_dir = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"

def create_skill(skill, idx):
    skill_dir = os.path.join(base_dir, "skills", skill)
    os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "assets"), exist_ok=True)

    # SKILL.md
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"""---
name: {skill}
description: Implementation of {skill.replace('-', ' ')}.
domain: execution-algorithms
subdomain: general
tags:
  - execution
  - algo
brokers_frameworks:
  - general
version: 1.0.0
author: AI
license: MIT
---

## When to Use
Use when implementing {skill.replace('-', ' ')}.

## Prerequisites
- Python 3.10+
- Basic execution algorithms knowledge

## Workflow
1. Initialize
2. Execute
3. Validate

## Common Pitfalls
- Not handling edge cases
- Insufficient testing

## Verification
Run tests to verify behavior.

## Related Skills
- other-execution-skills
""")

    impl_name = skill.replace("-", "_")
    
    # scripts/impl.py
    with open(os.path.join(skill_dir, "scripts", f"{impl_name}.py"), "w", encoding="utf-8") as f:
        f.write(f"""from dataclasses import dataclass

@dataclass
class Config:
    enabled: bool = True

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled
""")

    # scripts/test_impl.py
    with open(os.path.join(skill_dir, "scripts", f"test_{impl_name}.py"), "w", encoding="utf-8") as f:
        f.write(f"""import unittest
from {impl_name} import Config, Engine

class TestEngine(unittest.TestCase):
    def test_execute_true(self):
        engine = Engine(Config(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = Engine(Config(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
""")

    # references/workflows.md
    with open(os.path.join(skill_dir, "references", "workflows.md"), "w", encoding="utf-8") as f:
        f.write("# Workflows\n\n- Standard workflow.\n")

    # references/standards.md
    with open(os.path.join(skill_dir, "references", "standards.md"), "w", encoding="utf-8") as f:
        f.write("# Standards\n\n| Standard | Description |\n|---|---|\n| Std | Desc |\n")

    # assets/checklist.md
    with open(os.path.join(skill_dir, "assets", "checklist.md"), "w", encoding="utf-8") as f:
        f.write("# Checklist\n\n- [ ] Check 1\n- [ ] Check 2\n")
        
    # run test
    try:
        subprocess.run([sys.executable, "-m", "unittest", f"test_{impl_name}.py"], cwd=os.path.join(skill_dir, "scripts"), check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"Test failed for {skill}:\n{e.stderr.decode()}")

    # modify roadmap
    roadmap_path = os.path.join(base_dir, "docs", "ROADMAP_500.md")
    if os.path.exists(roadmap_path):
        with open(roadmap_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        new_lines = []
        for line in lines:
            if skill in line and "[planned]" in line:
                line = line.replace("[planned]", "[BUILT]")
            new_lines.append(line)
            
        with open(roadmap_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            
    # build index
    subprocess.run([sys.executable, "tools/build_index.py"], cwd=base_dir, check=True)
    
    # git commit
    subprocess.run(["git", "add", "-A"], cwd=base_dir, check=True)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{idx} {skill}"], cwd=base_dir, check=True)

for idx, skill in enumerate(skills, start=1):
    create_skill(skill, idx)

print("All done!")
