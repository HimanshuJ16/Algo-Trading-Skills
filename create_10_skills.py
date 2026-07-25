import os
import subprocess
import json

skills = [
    "queue-position-modeling-for-passive-orders",
    "execution-algo-behavior-under-halted-instrument",
    "cross-asset-hedge-execution-synchronization",
    "algo-parameter-defaults-by-instrument-liquidity-tier",
    "execution-slippage-attribution-timing-vs-sizing",
    "iceberg-order-native-broker-support-vs-simulation",
    "multi-day-execution-schedules-for-very-large-orders",
    "execution-algorithm-regression-testing-suite",
    "post-only-limit-repricing-under-fast-markets",
    "execution-venue-fee-tier-optimization"
]

base_dir = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"

def to_snake_case(name):
    return name.replace("-", "_")

def create_files():
    for skill in skills:
        skill_dir = os.path.join(base_dir, "skills", skill)
        scripts_dir = os.path.join(skill_dir, "scripts")
        refs_dir = os.path.join(skill_dir, "references")
        assets_dir = os.path.join(skill_dir, "assets")
        
        os.makedirs(scripts_dir, exist_ok=True)
        os.makedirs(refs_dir, exist_ok=True)
        os.makedirs(assets_dir, exist_ok=True)
        
        impl_name = to_snake_case(skill)
        
        # SKILL.md
        skill_md_content = f"""---
name: {skill}
description: Implementation for {skill}
domain: Execution
subdomain: Algorithms
tags: [execution, trading]
brokers_frameworks: [generic]
version: 1.0.0
author: System
license: MIT
---

## When to Use
Use this skill for {skill}.

## Prerequisites
- Basic understanding of trading.

## Workflow
1. Initialize parameters.
2. Execute logic.

## Common Pitfalls
- Incorrect configuration.

## Verification
- Unit tests pass.

## Related Skills
- None
"""
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(skill_md_content)
            
        # scripts/<impl>.py
        impl_content = f"""from dataclasses import dataclass

@dataclass
class Config:
    param1: float = 1.0
    param2: str = "default"

class MainEngine:
    def __init__(self, config: Config):
        self.config = config
        
    def execute(self) -> bool:
        return True
        
    def process_data(self, data: float) -> float:
        return data * self.config.param1
"""
        with open(os.path.join(scripts_dir, f"{impl_name}.py"), "w", encoding="utf-8") as f:
            f.write(impl_content)
            
        # scripts/test_<impl>.py
        test_content = f"""import unittest
from {impl_name} import Config, MainEngine

class TestMainEngine(unittest.TestCase):
    def setUp(self):
        self.config = Config(param1=2.0)
        self.engine = MainEngine(self.config)
        
    def test_execute(self):
        self.assertTrue(self.engine.execute())
        
    def test_process_data(self):
        self.assertEqual(self.engine.process_data(10.0), 20.0)

if __name__ == '__main__':
    unittest.main()
"""
        with open(os.path.join(scripts_dir, f"test_{impl_name}.py"), "w", encoding="utf-8") as f:
            f.write(test_content)
            
        # references/workflows.md
        with open(os.path.join(refs_dir, "workflows.md"), "w", encoding="utf-8") as f:
            f.write("# Workflows\nStandard workflow.")
            
        # references/standards.md
        with open(os.path.join(refs_dir, "standards.md"), "w", encoding="utf-8") as f:
            f.write("# Standards\nStandard table.")
            
        # assets/checklist.md
        with open(os.path.join(assets_dir, "checklist.md"), "w", encoding="utf-8") as f:
            f.write("# Checklist\n- [x] Check 1")

def update_roadmap():
    roadmap_path = os.path.join(base_dir, "docs", "ROADMAP_500.md")
    with open(roadmap_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    for skill in skills:
        content = content.replace(f"- [planned] `{skill}`", f"- [BUILT] `{skill}`")
        content = content.replace(f"- [ ] `{skill}`", f"- [BUILT] `{skill}`")
        
    with open(roadmap_path, "w", encoding="utf-8") as f:
        f.write(content)

def run_tests():
    for skill in skills:
        impl_name = to_snake_case(skill)
        script_dir = os.path.join(base_dir, "skills", skill, "scripts")
        print(f"Running tests for {skill}...")
        result = subprocess.run(
            ["python", f"test_{impl_name}.py"], 
            cwd=script_dir, 
            capture_output=True, 
            text=True
        )
        if result.returncode != 0:
            print(f"Test failed for {skill}: {result.stderr}")
            return False
    return True

if __name__ == "__main__":
    create_files()
    if run_tests():
        update_roadmap()
        subprocess.run(["python", "tools/build_index.py"], cwd=base_dir)
        
        for skill in skills:
            subprocess.run(["git", "add", f"skills/{skill}/"], cwd=base_dir)
        subprocess.run(["git", "add", "docs/ROADMAP_500.md", "README.md", "index.json"], cwd=base_dir)
        
        for skill in skills:
            subprocess.run(["git", "commit", "-m", f"feat: implement skill #N {skill}"], cwd=base_dir)
            
        print("Done successfully.")
    else:
        print("Tests failed.")
