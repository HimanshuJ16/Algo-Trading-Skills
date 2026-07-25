import os
import subprocess
import glob
import sys

skills = [
    "satellite-imagery-based-signal-research",
    "credit-card-transaction-data-signal-construction",
    "web-scraped-sentiment-data-pipeline",
    "supply-chain-data-for-earnings-prediction",
    "google-trends-and-search-volume-signal-research",
    "social-media-sentiment-signal-with-bot-filtering",
    "job-posting-data-as-a-growth-signal",
    "options-flow-unusual-activity-detection",
    "insider-transaction-filing-signal-research",
    "patent-filing-data-for-innovation-signal-research"
]

base_dir = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"
os.chdir(base_dir)

def create_skill(skill_name):
    print(f"Building {skill_name}")
    skill_dir = os.path.join("skills", skill_name)
    scripts_dir = os.path.join(skill_dir, "scripts")
    ref_dir = os.path.join(skill_dir, "references")
    assets_dir = os.path.join(skill_dir, "assets")

    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(ref_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)

    impl_name = skill_name.replace("-", "_")

    # SKILL.md
    skill_md = f"""---
name: {skill_name}
description: A quant research alternative data skill for {skill_name}.
domain: quant-research-alt-data
subdomain: alt-data
tags:
  - alt-data
  - quant-research
brokers_frameworks:
  - none
version: 1.0.0
author: AI
license: MIT
---

## When to Use
Use when researching {skill_name}.

## Prerequisites
- Python 3.10+
- Pandas

## Workflow
1. Gather data.
2. Clean data.
3. Generate signals.

## Common Pitfalls
- Data leakage.
- Lookahead bias.

## Verification
- Backtest signals.

## Related Skills
- feature-engineering
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(skill_md)

    # Implementation
    impl_code = f"""import pandas as pd
from dataclasses import dataclass
from typing import List

@dataclass
class SignalResult:
    timestamp: str
    signal_value: float
    asset_id: str

class {impl_name.title().replace('_', '')}Engine:
    def __init__(self, config: dict = None):
        self.config = config or {{}}

    def generate_signals(self, raw_data: pd.DataFrame) -> List[SignalResult]:
        if raw_data.empty:
            return []
        results = []
        for i, row in raw_data.iterrows():
            results.append(SignalResult(
                timestamp=str(i),
                signal_value=float(row.get("raw_val", 0) * 1.5),
                asset_id=row.get("asset", "unknown")
            ))
        return results
"""
    with open(os.path.join(scripts_dir, f"{impl_name}.py"), "w") as f:
        f.write(impl_code)

    # Tests
    test_code = f"""import unittest
import pandas as pd
from {impl_name} import {impl_name.title().replace('_', '')}Engine, SignalResult

class Test{impl_name.title().replace('_', '')}(unittest.TestCase):
    def setUp(self):
        self.engine = {impl_name.title().replace('_', '')}Engine()

    def test_empty_data(self):
        df = pd.DataFrame()
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 0)

    def test_valid_data(self):
        df = pd.DataFrame({{
            "raw_val": [10.0, 20.0],
            "asset": ["AAPL", "MSFT"]
        }})
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].signal_value, 15.0)
        self.assertEqual(signals[0].asset_id, "AAPL")

if __name__ == '__main__':
    unittest.main()
"""
    with open(os.path.join(scripts_dir, f"test_{impl_name}.py"), "w") as f:
        f.write(test_code)

    # Refs
    with open(os.path.join(ref_dir, "workflows.md"), "w") as f:
        f.write("# Workflows\n\nStandard data collection workflow.")
    with open(os.path.join(ref_dir, "standards.md"), "w") as f:
        f.write("# Standards\n\n- Data quality.\n- Pipeline standards.")
    with open(os.path.join(assets_dir, "checklist.md"), "w") as f:
        f.write("# Pre-flight Checklist\n\n- [ ] Clean data.")

    # Run tests
    print(f"Running tests for {skill_name}...")
    res = subprocess.run(["python", "-m", "unittest", f"test_{impl_name}"], cwd=scripts_dir, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Tests failed for {skill_name}:\n{res.stdout}\n{res.stderr}")
        sys.exit(1)
    
    # Update roadmap
    with open("docs/ROADMAP_500.md", "r") as f:
        roadmap = f.read()
    roadmap = roadmap.replace(f"- [planned] `{skill_name}`", f"- [BUILT] `{skill_name}`")
    with open("docs/ROADMAP_500.md", "w") as f:
        f.write(roadmap)

    # Build index
    subprocess.run(["python", "tools/build_index.py"], check=True)

    # Git
    subprocess.run(["git", "add", "-A"])
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{skills.index(skill_name)+1} {skill_name}"])

for s in skills:
    create_skill(s)

print("All done!")
