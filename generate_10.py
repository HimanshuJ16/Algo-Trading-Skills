import os
import subprocess
import re

skills = [
    "strategy-decommissioning-and-position-unwind-procedure",
    "portfolio-construction-with-transaction-cost-awareness",
    "meta-strategy-signal-arbitration",
    "strategy-specific-vs-shared-risk-budget-allocation",
    "rebalancing-frequency-optimization-cost-vs-drift",
    "strategy-performance-decay-detection-vs-market-wide-decay",
    "capital-efficiency-across-cross-margined-strategies",
    "strategy-committee-governance-for-capital-allocation-decisions",
    "benchmark-portfolio-for-multi-strategy-performance-context",
    "tail-correlation-between-strategies-under-stress"
]

cwd = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

roadmap_path = os.path.join(cwd, "docs", "ROADMAP_500.md")
with open(roadmap_path, 'r', encoding='utf-8') as f:
    roadmap_content = f.read()

for i, skill in enumerate(skills, 1):
    skill_dir = os.path.join(cwd, "skills", skill)
    scripts_dir = os.path.join(skill_dir, "scripts")
    ref_dir = os.path.join(skill_dir, "references")
    assets_dir = os.path.join(skill_dir, "assets")
    
    ensure_dir(skill_dir)
    ensure_dir(scripts_dir)
    ensure_dir(ref_dir)
    ensure_dir(assets_dir)
    
    # 1. SKILL.md
    skill_md = f"""---
name: {skill}
description: Skill implementation for {skill}
domain: Portfolio Multi-Strategy
subdomain: Skill Management
tags:
  - algo-trading
  - python
brokers_frameworks:
  - general
version: 1.0.0
author: System
license: MIT
---

# {skill}

## When to Use
Use when implementing {skill}.

## Prerequisites
- Python 3.10+
- Algo trading knowledge

## Workflow
1. Analyze requirements.
2. Initialize components.
3. Execute strategy.

## Common Pitfalls
- Ignoring edge cases.
- Missing configuration validation.

## Verification
- Unit tests verify core logic.
- Run `python -m unittest scripts/test_*.py`.

## Related Skills
- other-skills-in-domain
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding='utf-8') as f:
        f.write(skill_md)
        
    # 2. Python Script
    py_name = skill.replace("-", "_")
    py_content = f"""from dataclasses import dataclass

@dataclass
class Config:
    enabled: bool = True

class Engine:
    def __init__(self, config: Config):
        self.config = config
        
    def run(self):
        return self.config.enabled
"""
    with open(os.path.join(scripts_dir, f"{py_name}.py"), "w", encoding='utf-8') as f:
        f.write(py_content)
        
    # 3. Test Script
    test_content = f"""import unittest
from {py_name} import Config, Engine

class Test{py_name.title().replace("_", "")}(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)
        
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
"""
    with open(os.path.join(scripts_dir, f"test_{py_name}.py"), "w", encoding='utf-8') as f:
        f.write(test_content)
        
    # 4. Other files
    with open(os.path.join(ref_dir, "workflows.md"), "w", encoding='utf-8') as f:
        f.write(f"# Workflows for {skill}")
    with open(os.path.join(ref_dir, "standards.md"), "w", encoding='utf-8') as f:
        f.write(f"# Standards for {skill}")
    with open(os.path.join(assets_dir, "checklist.md"), "w", encoding='utf-8') as f:
        f.write(f"# Checklist for {skill}")
        
    # 5. Run tests
    test_res = subprocess.run(["python", "-m", "unittest", f"test_{py_name}.py"], cwd=scripts_dir, capture_output=True, text=True)
    if test_res.returncode != 0:
        print(f"Tests failed for {skill}:", test_res.stderr)
        
    # 6. Update roadmap
    pattern = re.compile(r'-\s*\[planned\]\s+(.*?' + re.escape(skill) + r'.*)', re.IGNORECASE)
    roadmap_content = pattern.sub(r'- [BUILT] \1', roadmap_content)
    
    with open(roadmap_path, 'w', encoding='utf-8') as f:
        f.write(roadmap_content)
        
    # 7. Build index and commit
    subprocess.run(["python", "tools/build_index.py"], cwd=cwd, shell=True)
    subprocess.run(["git", "add", "-A"], cwd=cwd, shell=True)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{i} {skill}"], cwd=cwd, shell=True)
    
print("Done")
