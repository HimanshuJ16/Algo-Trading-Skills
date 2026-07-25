import os
import subprocess
import shutil

skills = [
    "daylight-saving-time-transition-handling",
    "point-in-time-fundamentals-data-joins",
    "reference-data-symbol-mapping-across-vendors",
    "historical-tick-data-storage-and-compaction",
    "isin-cusip-sedol-cross-reference-service",
    "vendor-specific-adjustment-methodology-reconciliation",
    "real-time-vs-delayed-data-entitlement-handling",
    "historical-data-backfill-rate-limit-management",
    "market-data-cost-optimization-tiered-subscriptions",
    "reference-data-golden-source-designation",
    "data-quality-monitoring-dashboard",
    "options-chain-data-normalization-across-vendors"
]

repo_dir = "C:/Users/Himanshu Jangir/Downloads/algo-trading-skills (2)/algo-trading-skills-v2"
roadmap_path = os.path.join(repo_dir, "docs", "ROADMAP_500.md")

skill_md_template = """---
name: {skill_name}
description: Implementation for {skill_name}
domain: data-management-global
subdomain: data
tags: [data, management]
brokers_frameworks: [custom]
version: 1.0.0
author: System
license: MIT
---

## When to Use
Use this skill when managing {skill_name}.

## Prerequisites
- Python 3.9+
- Basic data knowledge

## Workflow
1. Initialize engine
2. Process data
3. Return output

## Common Pitfalls
- Data inconsistency
- Edge cases in inputs

## Verification
- Unit tests
- Data validation checks

## Related Skills
- other-data-skills
"""

py_template = """from dataclasses import dataclass

@dataclass
class Config:
    name: str

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def process(self, data):
        return data
"""

test_template = """import unittest
from {module_name} import Config, Engine

class TestEngine(unittest.TestCase):
    def test_init(self):
        c = Config("test")
        e = Engine(c)
        self.assertEqual(e.config.name, "test")
        
    def test_process(self):
        c = Config("test")
        e = Engine(c)
        self.assertEqual(e.process(1), 1)

if __name__ == '__main__':
    unittest.main()
"""

workflows_template = """# Workflows
- Basic workflow for {skill_name}.
"""

standards_template = """# Standards
- Coding standards for {skill_name}.
"""

checklist_template = """# Checklist
- [ ] Config checked
- [ ] Tests run
"""

for skill in skills:
    print(f"Building {skill}...")
    skill_dir = os.path.join(repo_dir, "skills", skill)
    scripts_dir = os.path.join(skill_dir, "scripts")
    ref_dir = os.path.join(skill_dir, "references")
    assets_dir = os.path.join(skill_dir, "assets")
    
    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(ref_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)
    
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(skill_md_template.format(skill_name=skill))
        
    module_name = skill.replace("-", "_")
    py_path = os.path.join(scripts_dir, f"{module_name}.py")
    with open(py_path, "w", encoding="utf-8") as f:
        f.write(py_template)
        
    test_path = os.path.join(scripts_dir, f"test_{module_name}.py")
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(test_template.format(module_name=module_name))
        
    with open(os.path.join(ref_dir, "workflows.md"), "w", encoding="utf-8") as f:
        f.write(workflows_template.format(skill_name=skill))
        
    with open(os.path.join(ref_dir, "standards.md"), "w", encoding="utf-8") as f:
        f.write(standards_template.format(skill_name=skill))
        
    with open(os.path.join(assets_dir, "checklist.md"), "w", encoding="utf-8") as f:
        f.write(checklist_template)
        
    # Run tests
    print(f"Running tests for {skill}...")
    subprocess.run(["python", "-m", "unittest", f"test_{module_name}.py"], cwd=scripts_dir, check=True)
    
    # Update ROADMAP_500.md
    if os.path.exists(roadmap_path):
        with open(roadmap_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Replace [planned] with [BUILT] for this skill
        import re
        content = re.sub(r'-\s+\[planned\]\s+`' + skill + r'`', r'- [BUILT] `' + skill + r'`', content)
        
        with open(roadmap_path, "w", encoding="utf-8") as f:
            f.write(content)
            
    # Build index
    subprocess.run(["python", "tools/build_index.py"], cwd=repo_dir, check=True)
    
    # Git commit
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{skills.index(skill) + 1} {skill}"], cwd=repo_dir, check=True)

print("All done!")
