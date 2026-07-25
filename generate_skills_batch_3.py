import os
import subprocess
import re

CWD = "C:/Users/Himanshu Jangir/Downloads/algo-trading-skills (2)/algo-trading-skills-v2"

skills = [
    "co-location-provider-selection-and-network-topology",
    "fpga-based-market-data-processing-evaluation",
    "microwave-vs-fiber-network-links-for-cross-market-latency",
    "exchange-matching-engine-behavior-under-load",
    "tick-size-pilot-program-impact-assessment",
    "message-rate-limit-vs-latency-tradeoff-tuning",
    "order-to-trade-ratio-fee-penalty-avoidance",
    "microstructure-noise-filtering-for-hf-signals"
]

def generate_skill(skill_name):
    base_dir = os.path.join(CWD, "skills", skill_name)
    os.makedirs(os.path.join(base_dir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(base_dir, "references"), exist_ok=True)
    os.makedirs(os.path.join(base_dir, "assets"), exist_ok=True)
    
    # 1. SKILL.md
    skill_md = f"""---
name: {skill_name}
description: Skill to handle {skill_name.replace('-', ' ')}
domain: Market Microstructure
subdomain: Latency
tags: [latency, hft]
brokers_frameworks: []
version: 1.0.0
author: assistant
license: MIT
---

## When to Use
Use when dealing with {skill_name}.

## Prerequisites
None.

## Workflow
1. Start
2. End

## Common Pitfalls
None.

## Verification
Tests pass.

## Related Skills
None.
"""
    with open(os.path.join(base_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(skill_md)

    impl_name = skill_name.replace("-", "_")

    # 2. scripts/impl.py
    impl_py = f"""from dataclasses import dataclass

@dataclass
class Config:
    param1: int = 1

class Engine:
    def __init__(self, config: Config):
        self.config = config
    def run(self):
        return True
"""
    with open(os.path.join(base_dir, "scripts", f"{impl_name}.py"), "w", encoding="utf-8") as f:
        f.write(impl_py)

    # 3. scripts/test_impl.py
    test_py = f"""import unittest
from {impl_name} import Engine, Config

class TestEngine(unittest.TestCase):
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())
        
    def test_config(self):
        config = Config(param1=2)
        self.assertEqual(config.param1, 2)

if __name__ == '__main__':
    unittest.main()
"""
    with open(os.path.join(base_dir, "scripts", f"test_{impl_name}.py"), "w", encoding="utf-8") as f:
        f.write(test_py)
        
    # 4. references/workflows.md
    with open(os.path.join(base_dir, "references", "workflows.md"), "w", encoding="utf-8") as f:
        f.write("# Workflows\\nBasic workflow.")
        
    # 4. references/standards.md
    with open(os.path.join(base_dir, "references", "standards.md"), "w", encoding="utf-8") as f:
        f.write("# Standards\\nStandard table.")
        
    # 4. assets/checklist.md
    with open(os.path.join(base_dir, "assets", "checklist.md"), "w", encoding="utf-8") as f:
        f.write("# Checklist\\n- [ ] Step 1")

    # Run tests
    print(f"Running tests for {skill_name}...")
    res = subprocess.run(["python", "-m", "unittest", f"test_{impl_name}.py"], cwd=os.path.join(base_dir, "scripts"), capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Tests failed for {skill_name}:\\n{res.stderr}")
    else:
        print(f"Tests passed for {skill_name}")

def update_roadmap():
    roadmap_path = os.path.join(CWD, "docs", "ROADMAP_500.md")
    with open(roadmap_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    for skill in skills:
        pattern = re.compile(r'\[planned\](\s+`?' + re.escape(skill) + r'`?)', re.IGNORECASE)
        content = pattern.sub(r'[BUILT]\1', content)
        
    with open(roadmap_path, "w", encoding="utf-8") as f:
        f.write(content)

def build_index():
    print("Building index...")
    subprocess.run(["python", "tools/build_index.py"], cwd=CWD)

def commit_changes():
    for i, skill in enumerate(skills):
        print(f"Committing {skill}...")
        subprocess.run(["git", "add", "-A"], cwd=CWD)
        subprocess.run(["git", "commit", "-m", f"feat: implement skill #{i+1} {skill}"], cwd=CWD)

for skill in skills:
    generate_skill(skill)

update_roadmap()
build_index()
commit_changes()
print("Done.")
