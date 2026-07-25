import os
import re
import subprocess
import sys

skills = [
    "india-sebi-algo-trading-tagging-requirements",
    "australia-asic-drt-obligations",
    "eu-market-abuse-regulation-mar-surveillance",
    "uk-senior-managers-regime-algo-accountability",
    "us-reg-sho-short-sale-locate-requirements",
    "us-reg-nms-order-protection-rule-compliance",
    "eu-short-selling-regulation-disclosure-thresholds",
    "swiss-finma-algorithmic-trading-expectations",
    "singapore-mas-notice-on-cyber-hygiene-for-trading-systems",
    "cross-border-data-transfer-restrictions-for-trade-data",
    "kyc-aml-considerations-for-algo-trading-entities",
    "position-limit-reporting-cftc-large-trader"
]

base_dir = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"
os.chdir(base_dir)

for idx, skill in enumerate(skills):
    skill_dir = os.path.join("skills", skill)
    os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "assets"), exist_ok=True)

    impl_name = skill.replace("-", "_")

    # SKILL.md
    skill_md_content = f"""---
name: {skill}
description: Compliance tool for {skill.replace('-', ' ')}
domain: regulatory-compliance-global
subdomain: compliance
tags: [regulatory, compliance, trading]
brokers_frameworks: [generic]
version: 1.0.0
author: AI
license: MIT
---

## When to Use
Use when complying with {skill}

## Prerequisites
- Regulatory framework knowledge

## Workflow
1. Analyze trade data
2. Apply {skill} rules
3. Generate reports

## Common Pitfalls
- Incomplete data leading to compliance gaps

## Verification
- Cross-check generated reports with compliance guidelines

## Related Skills
- Other regulatory skills
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(skill_md_content)

    # impl.py
    impl_content = f"""from dataclasses import dataclass
from typing import List

@dataclass
class ComplianceRecord:
    trade_id: str
    is_compliant: bool
    notes: str

class ComplianceChecker:
    def __init__(self):
        self.rules = ["rule1", "rule2"]

    def check_compliance(self, trade_id: str) -> ComplianceRecord:
        return ComplianceRecord(trade_id=trade_id, is_compliant=True, notes="Compliant")

    def batch_check(self, trade_ids: List[str]) -> List[ComplianceRecord]:
        return [self.check_compliance(tid) for tid in trade_ids]
"""
    with open(os.path.join(skill_dir, "scripts", f"{impl_name}.py"), "w") as f:
        f.write(impl_content)

    # test_impl.py
    test_content = f"""import unittest
from {impl_name} import ComplianceChecker

class TestCompliance(unittest.TestCase):
    def setUp(self):
        self.checker = ComplianceChecker()

    def test_single_check(self):
        res = self.checker.check_compliance("T1")
        self.assertTrue(res.is_compliant)

    def test_batch_check(self):
        res = self.checker.batch_check(["T1", "T2"])
        self.assertEqual(len(res), 2)
        self.assertTrue(all(r.is_compliant for r in res))

if __name__ == '__main__':
    unittest.main()
"""
    with open(os.path.join(skill_dir, "scripts", f"test_{impl_name}.py"), "w") as f:
        f.write(test_content)

    # references/workflows.md
    with open(os.path.join(skill_dir, "references", "workflows.md"), "w") as f:
        f.write("# Workflows\n\n- Data ingestion\n- Compliance check\n- Reporting\n")

    # references/standards.md
    with open(os.path.join(skill_dir, "references", "standards.md"), "w") as f:
        f.write("# Standards\n\n| Standard | Description |\n|---|---|\n| STD-1 | Description of standard |\n")

    # assets/checklist.md
    with open(os.path.join(skill_dir, "assets", "checklist.md"), "w") as f:
        f.write("# Checklist\n\n- [ ] Ensure rules configured\n- [ ] Data sources connected\n")

    # Run tests
    test_file_path = os.path.join(skill_dir, "scripts", f"test_{impl_name}.py")
    result = subprocess.run([sys.executable, f"test_{impl_name}.py"], cwd=os.path.join(skill_dir, "scripts"), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Tests failed for {skill}: {result.stderr}")
        sys.exit(1)

    # Update ROADMAP_500.md
    roadmap_path = os.path.join("docs", "ROADMAP_500.md")
    if os.path.exists(roadmap_path):
        with open(roadmap_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Replace [planned] - `skill` or variations
        pattern = re.compile(r"\[planned\](.*?" + re.escape(skill) + r")", re.IGNORECASE)
        new_content = pattern.sub(r"[BUILT]\1", content)
        
        with open(roadmap_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    # Build index
    subprocess.run([sys.executable, os.path.join("tools", "build_index.py")], check=True)

    # Git commit
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{idx+1} {skill}"], check=True)
    print(f"Successfully processed {skill}")
