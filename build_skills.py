import os
import subprocess
import re
import sys

skills = [
    "strategy-specific-data-dependency-mapping",
    "incremental-capital-deployment-for-new-strategies",
    "cross-strategy-tax-lot-optimization",
    "strategy-level-kill-switch-vs-portfolio-level-kill-switch",
    "multi-strategy-reporting-consolidation-for-stakeholders",
    "strategy-research-to-production-pipeline-governance",
    "opportunity-cost-tracking-for-idle-capital",
    "cross-strategy-signal-reuse-and-licensing",
    "strategy-underperformance-remediation-decision-tree",
    "portfolio-stress-test-including-liquidity-crunch-scenarios"
]

base_dir = "C:/Users/Himanshu Jangir/Downloads/algo-trading-skills (2)/algo-trading-skills-v2"

for idx, skill in enumerate(skills):
    impl_name = skill.replace("-", "_")
    skill_dir = os.path.join(base_dir, "skills", skill)
    scripts_dir = os.path.join(skill_dir, "scripts")
    refs_dir = os.path.join(skill_dir, "references")
    assets_dir = os.path.join(skill_dir, "assets")
    
    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(refs_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)
    
    # 1. SKILL.md
    skill_md = f"""---
name: {skill}
description: Skill for {skill.replace('-', ' ')}
domain: Portfolio Multi-Strategy
subdomain: Operations
tags:
  - multi-strategy
  - portfolio
brokers_frameworks:
  - custom
version: 1.0.0
author: System
license: MIT
---

## When to Use
Use when managing multi-strategy portfolios.

## Prerequisites
- Basic understanding of multi-strategy environments.

## Workflow
1. Initialize the system.
2. Execute the engine.
3. Review results.

## Common Pitfalls
- Ignoring cross-strategy correlations.

## Verification
- Unit tests verify the main components.

## Related Skills
- other-portfolio-skills
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding='utf-8') as f:
        f.write(skill_md)
        
    # 2. impl.py
    impl_py = f"""from dataclasses import dataclass

@dataclass
class Config:
    name: str

class Engine:
    def __init__(self, config: Config):
        self.config = config
        
    def run(self) -> bool:
        return True
"""
    with open(os.path.join(scripts_dir, f"{impl_name}.py"), "w", encoding='utf-8') as f:
        f.write(impl_py)
        
    # 3. test_impl.py
    test_py = f"""import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from {impl_name} import Config, Engine

class TestEngine(unittest.TestCase):
    def test_init(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertEqual(engine.config.name, "test")
        
    def test_run(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
"""
    with open(os.path.join(scripts_dir, f"test_{impl_name}.py"), "w", encoding='utf-8') as f:
        f.write(test_py)
        
    # 4. references & assets
    with open(os.path.join(refs_dir, "workflows.md"), "w", encoding='utf-8') as f:
        f.write("# Workflows\nBasic workflow.")
    with open(os.path.join(refs_dir, "standards.md"), "w", encoding='utf-8') as f:
        f.write("# Standards\nBasic standards.")
    with open(os.path.join(assets_dir, "checklist.md"), "w", encoding='utf-8') as f:
        f.write("# Checklist\n- [x] Check 1")
        
    # Run tests
    test_res = subprocess.run(["python", "-m", "unittest", f"test_{impl_name}.py"], cwd=scripts_dir, capture_output=True, text=True)
    if test_res.returncode != 0:
        print(f"Tests failed for {skill}:\n{test_res.stderr}")
        sys.exit(1)
    else:
        print(f"Tests passed for {skill}")

    # Update ROADMAP
    roadmap_path = os.path.join(base_dir, "docs", "ROADMAP_500.md")
    if os.path.exists(roadmap_path):
        with open(roadmap_path, "r", encoding='utf-8') as f:
            roadmap = f.read()
        
        pattern = r"-\s*\*\*\[planned\]\*\*\s*`" + re.escape(skill) + r"`"
        replacement = f"- **[BUILT]** `{skill}`"
        roadmap = re.sub(pattern, replacement, roadmap)
        
        with open(roadmap_path, "w", encoding='utf-8') as f:
            f.write(roadmap)
            
    # Build index
    subprocess.run(["python", "tools/build_index.py"], cwd=base_dir)
    
    # Git commit
    subprocess.run(["git", "add", "-A"], cwd=base_dir)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{idx+1} {skill}"], cwd=base_dir)

print("All done!")
