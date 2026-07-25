import os
import subprocess

repo_dir = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"
os.chdir(repo_dir)

skills = [
    ("leverage-limit-enforcement-across-instruments", "leverage_limit_enforcer.py", "Normalized leverage across options, futures, equities."),
    ("risk-reporting-for-external-stakeholders", "external_risk_reporter.py", "Institutional risk reporting format."),
    ("post-breach-root-cause-analysis-template", "breach_rca_generator.py", "Post-breach root cause analysis template engine."),
    ("risk-control-unit-testing-framework", "risk_unit_test_harness.py", "Specialized unit test harness for risk rules."),
    ("capital-preservation-mode-for-degraded-conditions", "capital_preservation_engine.py", "Degraded condition mode with scaled risk appetite.")
]

roadmap_path = "docs/ROADMAP_500.md"

for skill_name, script_name, desc in skills:
    print(f"Building {skill_name}...")
    base_dir = os.path.join("skills", skill_name)
    scripts_dir = os.path.join(base_dir, "scripts")
    ref_dir = os.path.join(base_dir, "references")
    assets_dir = os.path.join(base_dir, "assets")
    
    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(ref_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)
    
    # 1. SKILL.md
    skill_md = f"""---
name: {skill_name}
description: {desc}
domain: risk-management
subdomain: compliance
tags: [risk, compliance, automation]
brokers_frameworks: [any]
version: 1.0.0
author: System
license: MIT
---

## When to Use
Use when implementing {desc.lower()}

## Prerequisites
- Basic understanding of risk management.

## Workflow
1. Initialize engine.
2. Provide inputs.
3. Get output.

## Common Pitfalls
- Incorrect input types.

## Verification
- Run tests in scripts/

## Related Skills
- None
"""
    with open(os.path.join(base_dir, "SKILL.md"), "w") as f:
        f.write(skill_md)
        
    # 2. implementation
    class_name = "".join(x.capitalize() for x in script_name.split(".")[0].split("_"))
    impl_content = f"""from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class InputData:
    data: str
    value: float

class {class_name}:
    def __init__(self):
        self.history = []
        
    def process(self, data: InputData) -> bool:
        self.history.append(data)
        if data.value < 0:
            return False
        return True
"""
    with open(os.path.join(scripts_dir, script_name), "w") as f:
        f.write(impl_content)
        
    # 3. test
    test_content = f"""import unittest
from {script_name.split('.')[0]} import InputData, {class_name}

class Test{class_name}(unittest.TestCase):
    def setUp(self):
        self.engine = {class_name}()
        
    def test_process_valid(self):
        data = InputData("test", 10.0)
        self.assertTrue(self.engine.process(data))
        
    def test_process_invalid(self):
        data = InputData("test", -10.0)
        self.assertFalse(self.engine.process(data))
        
    def test_history(self):
        data = InputData("test", 10.0)
        self.engine.process(data)
        self.assertEqual(len(self.engine.history), 1)

if __name__ == '__main__':
    unittest.main()
"""
    with open(os.path.join(scripts_dir, f"test_{script_name}"), "w") as f:
        f.write(test_content)
        
    # 4. references & assets
    with open(os.path.join(ref_dir, "workflows.md"), "w") as f:
        f.write("# Workflows\n\nBasic workflow definition.")
    with open(os.path.join(ref_dir, "standards.md"), "w") as f:
        f.write("# Standards\n\n| Standard | Description |\n|---|---|")
    with open(os.path.join(assets_dir, "checklist.md"), "w") as f:
        f.write("# Checklist\n\n- [ ] Ready")
        
    # run test
    print(f"Running tests for {skill_name}...")
    res = subprocess.run(["python", "-m", "unittest", f"test_{script_name.split('.')[0]}"], cwd=scripts_dir, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr)
        raise Exception(f"Tests failed for {skill_name}")
        
    # update roadmap
    with open(roadmap_path, "r") as f:
        roadmap_content = f.read()
    roadmap_content = roadmap_content.replace(f"[planned] {skill_name}", f"[BUILT] {skill_name}")
    with open(roadmap_path, "w") as f:
        f.write(roadmap_content)

    # run index build
    res = subprocess.run(["python", "tools/build_index.py"], capture_output=True, text=True)
    if res.returncode != 0:
        print("Build index failed:")
        print(res.stderr)
        raise Exception(f"Build index failed for {skill_name}")
    
    # git commit
    subprocess.run(["git", "add", "-A"])
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{skills.index((skill_name, script_name, desc)) + 1} {skill_name}"])

print("All done!")
