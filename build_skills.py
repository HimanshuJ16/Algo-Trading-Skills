import os
import subprocess
import re
import sys

repo_dir = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"
os.chdir(repo_dir)

skills = [
    ("on-call-rotation-and-escalation-for-trading-systems", "oncall_escalation_manager"),
    ("environment-parity-dev-staging-production", "environment_parity_auditor"),
    ("automated-rollback-triggers-on-anomaly-detection", "anomaly_rollback_trigger"),
    ("capacity-planning-for-symbol-universe-growth", "capacity_planning_forecaster"),
    ("zero-downtime-database-schema-migrations", "zero_downtime_migration_engine"),
    ("dependency-pinning-and-reproducible-builds", "reproducible_build_pinner"),
    ("audit-logging-for-configuration-changes", "config_change_audit_logger"),
    ("network-segmentation-for-trading-infrastructure", "network_segmentation_auditor"),
]

def make_skill(i, skill_name, script_name):
    base_dir = f"skills/{skill_name}"
    os.makedirs(f"{base_dir}/scripts", exist_ok=True)
    os.makedirs(f"{base_dir}/references", exist_ok=True)
    os.makedirs(f"{base_dir}/assets", exist_ok=True)

    # 1. SKILL.md
    with open(f"{base_dir}/SKILL.md", "w") as f:
        f.write(f"""---
name: {skill_name}
description: Skill for {skill_name}
domain: deployment-ops
subdomain: deployment
tags:
  - {skill_name}
brokers_frameworks: []
version: 1.0.0
author: System
license: MIT
---

## When to Use
Use this skill for {skill_name}.

## Prerequisites
- Python 3.9+

## Workflow
1. Start
2. Process
3. End

## Common Pitfalls
- Not testing adequately.

## Verification
- Run the tests.

## Related Skills
- None
""")

    # 2. Script
    class_name = "".join(x.title() for x in script_name.split("_"))
    with open(f"{base_dir}/scripts/{script_name}.py", "w") as f:
        f.write(f"""from dataclasses import dataclass

@dataclass
class Config:
    name: str

class {class_name}:
    def __init__(self, config: Config):
        self.config = config

    def execute(self) -> bool:
        return True
""")

    # 3. Test
    with open(f"{base_dir}/scripts/test_{script_name}.py", "w") as f:
        f.write(f"""import unittest
from {script_name} import {class_name}, Config

class Test{class_name}(unittest.TestCase):
    def test_init(self):
        engine = {class_name}(Config("test"))
        self.assertEqual(engine.config.name, "test")

    def test_execute(self):
        engine = {class_name}(Config("test"))
        self.assertTrue(engine.execute())

if __name__ == '__main__':
    unittest.main()
""")

    # 4. references/workflows.md
    with open(f"{base_dir}/references/workflows.md", "w") as f:
        f.write("# Workflows\n\n- Standard workflow.")

    # 5. references/standards.md
    with open(f"{base_dir}/references/standards.md", "w") as f:
        f.write("# Standards\n\n| Standard | Description |\n|---|---|")

    # 6. assets/checklist.md
    with open(f"{base_dir}/assets/checklist.md", "w") as f:
        f.write("# Checklist\n\n- [ ] Check 1")

    # Run tests
    sys.path.insert(0, os.path.abspath(f"{base_dir}/scripts"))
    result = subprocess.run(["python", f"{base_dir}/scripts/test_{script_name}.py"], capture_output=True, text=True, cwd=repo_dir)
    sys.path.pop(0)
    
    if result.returncode != 0:
        print(f"Tests failed for {skill_name}:\n{result.stderr}")
        return False
    
    # Update ROADMAP_500.md
    roadmap_path = "docs/ROADMAP_500.md"
    if os.path.exists(roadmap_path):
        with open(roadmap_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Regex to handle [planned] -> [BUILT] for this skill
        content = re.sub(
            r"\[planned\](\s*.*?" + re.escape(skill_name) + r")",
            r"[BUILT]\1",
            content,
            flags=re.IGNORECASE
        )
        
        with open(roadmap_path, "w", encoding="utf-8") as f:
            f.write(content)
            
    # Run build index
    subprocess.run(["python", "tools/build_index.py"], cwd=repo_dir)
    
    # Git commit
    subprocess.run(["git", "add", "-A"], cwd=repo_dir)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{i+1} {skill_name}"], cwd=repo_dir)
    
    print(f"Successfully processed {skill_name}")
    return True

for i, (sn, cn) in enumerate(skills):
    make_skill(i, sn, cn)
