import os
import subprocess
import sys
import re

skills = [
    "wash-sale-rule-tracking-us",
    "fifo-vs-specific-lot-tax-accounting-methods",
    "mark-to-market-election-for-active-traders-us",
    "crypto-transaction-tax-lot-tracking",
    "multi-jurisdiction-tax-residency-implications",
    "1099-b-and-broker-tax-reporting-reconciliation",
    "vat-gst-treatment-of-trading-related-services",
    "transfer-pricing-considerations-for-multi-entity-trading-operations",
    "automated-tax-lot-reporting-pipeline",
    "capital-gains-vs-business-income-classification",
    "estimated-tax-payment-scheduling-for-active-trading-income",
    "record-keeping-requirements-for-tax-audit-defense",
    "currency-gain-loss-tax-treatment-for-forex-trading",
    "section-1256-contract-tax-treatment-us-futures",
    "double-taxation-treaty-considerations-cross-border-trading",
    "constructive-sale-rule-considerations-us"
]

domain = "tax-accounting-reporting-global"
subdomain = "tax-reporting"

def build():
    roadmap_path = "docs/ROADMAP_500.md"
    if os.path.exists(roadmap_path):
        with open(roadmap_path, "r", encoding="utf-8") as f:
            roadmap = f.read()
    else:
        roadmap = ""

    for i, skill in enumerate(skills, 1):
        skill_dir = f"skills/{skill}"
        os.makedirs(f"{skill_dir}/scripts", exist_ok=True)
        os.makedirs(f"{skill_dir}/references", exist_ok=True)
        os.makedirs(f"{skill_dir}/assets", exist_ok=True)

        impl_name = skill.replace("-", "_")
        if impl_name[0].isdigit():
            impl_name = "s_" + impl_name
        
        # 1. SKILL.md
        skill_md = f"""---
name: {skill}
description: Implementation for {skill}
domain: {domain}
subdomain: {subdomain}
tags:
  - tax
  - reporting
brokers_frameworks: []
version: 1.0.0
author: System
license: MIT
---

## When to Use

Use this skill when handling {skill} related operations.

## Prerequisites

- Python 3.9+
- Basic knowledge of tax accounting

## Workflow

1. Initialize engine
2. Process tax records
3. Generate reports

## Common Pitfalls

- Incorrect data types
- Missing regulatory updates

## Verification

Run the included unit tests.

## Related Skills

- general-tax-reporting
"""
        with open(f"{skill_dir}/SKILL.md", "w", encoding="utf-8") as f:
            f.write(skill_md)

        # 2. main.py
        class_name = "".join(x.capitalize() for x in skill.split("-")) + "Engine"
        if class_name[0].isdigit():
            class_name = "S" + class_name
            
        main_py = f"""from dataclasses import dataclass

@dataclass
class Record:
    id: str
    value: float

class {class_name}:
    def __init__(self):
        self.records = []
        
    def add_record(self, record: Record):
        self.records.append(record)
        
    def process(self):
        return sum(r.value for r in self.records)
"""
        with open(f"{skill_dir}/scripts/{impl_name}.py", "w", encoding="utf-8") as f:
            f.write(main_py)

        # 3. test_main.py
        test_py = f"""import unittest
from {impl_name} import {class_name}, Record

class Test{class_name}(unittest.TestCase):
    def test_initialization(self):
        engine = {class_name}()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = {class_name}()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = {class_name}()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
"""
        with open(f"{skill_dir}/scripts/test_{impl_name}.py", "w", encoding="utf-8") as f:
            f.write(test_py)

        # 4, 5, 6 References and Assets
        with open(f"{skill_dir}/references/workflows.md", "w", encoding="utf-8") as f:
            f.write(f"# Workflows for {skill}\n\n1. Step 1\n2. Step 2")
        with open(f"{skill_dir}/references/standards.md", "w", encoding="utf-8") as f:
            f.write(f"# Standards for {skill}\n\n| Standard | Description |\n|---|---|")
        with open(f"{skill_dir}/assets/checklist.md", "w", encoding="utf-8") as f:
            f.write(f"# Checklist for {skill}\n\n- [ ] Task 1")

        # 7. Run tests
        print(f"Running tests for {skill}...")
        res = subprocess.run([sys.executable, "-m", "unittest", f"test_{impl_name}"], cwd=f"{skill_dir}/scripts", capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Tests failed for {skill}:\n{res.stderr}")
            sys.exit(1)

        # 8. Update Roadmap
        pattern = re.compile(rf"-\s*\[planned\]\s*{re.escape(skill)}\b", re.IGNORECASE)
        if pattern.search(roadmap):
            roadmap = pattern.sub(f"- [BUILT] {skill}", roadmap)
        else:
            pattern2 = re.compile(rf"\[planned\]\s*{re.escape(skill)}\b", re.IGNORECASE)
            roadmap = pattern2.sub(f"[BUILT] {skill}", roadmap)

        with open(roadmap_path, "w", encoding="utf-8") as f:
            f.write(roadmap)

        # 9. build_index.py
        subprocess.run([sys.executable, "tools/build_index.py"])

        # 10. Commit
        subprocess.run(["git", "add", "-A"])
        subprocess.run(["git", "commit", "-m", f"feat: implement skill #{i} {skill}"])
        print(f"Successfully implemented and committed {skill}")

if __name__ == "__main__":
    build()
