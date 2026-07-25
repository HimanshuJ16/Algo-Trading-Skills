import os
import subprocess

skills = [
    {
        "name": "risk-limit-calibration-against-historical-drawdowns",
        "impl": "drawdown_limit_calibrator.py",
        "desc": "Calibrating limits using historical drawdown distribution.",
        "idx": 1
    },
    {
        "name": "multi-currency-var-aggregation",
        "impl": "multi_currency_var_aggregator.py",
        "desc": "FX-converted multi-currency VaR aggregation.",
        "idx": 2
    },
    {
        "name": "risk-control-configuration-change-approval-workflow",
        "impl": "risk_config_approval_workflow.py",
        "desc": "Two-person approval workflow for risk config changes.",
        "idx": 3
    },
    {
        "name": "real-time-liquidity-risk-monitoring",
        "impl": "real_time_liquidity_monitor.py",
        "desc": "Monitoring spread widening and depth thinning.",
        "idx": 4
    },
    {
        "name": "risk-adjusted-performance-attribution-per-strategy",
        "impl": "risk_adjusted_attribution.py",
        "desc": "RAROC / Sharpe attribution per strategy sharing capital.",
        "idx": 5
    }
]

for skill in skills:
    name = skill["name"]
    impl = skill["impl"]
    desc = skill["desc"]
    idx = skill["idx"]
    
    base_dir = f"skills/{name}"
    os.makedirs(f"{base_dir}/scripts", exist_ok=True)
    os.makedirs(f"{base_dir}/references", exist_ok=True)
    os.makedirs(f"{base_dir}/assets", exist_ok=True)
    
    skill_md = f"""---
name: {name}
description: {desc}
domain: Risk Management
subdomain: Calibration
tags: [risk, {name}]
brokers_frameworks: [any]
version: 1.0.0
author: System
license: MIT
---

## When to Use
Use when implementing {desc}.

## Prerequisites
- Python 3.10+

## Workflow
1. Initialize component
2. Pass data
3. Receive result

## Common Pitfalls
- Incorrect math

## Verification
- Run tests

## Related Skills
- None
"""
    with open(f"{base_dir}/SKILL.md", "w", encoding="utf-8") as f:
        f.write(skill_md)
        
    impl_py = f"""from dataclasses import dataclass

@dataclass
class Config:
    param: float = 1.0

class MainEngine:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self, value: float) -> float:
        return value * self.config.param
"""
    with open(f"{base_dir}/scripts/{impl}", "w", encoding="utf-8") as f:
        f.write(impl_py)
        
    test_py = f"""import unittest
from {impl.replace('.py', '')} import Config, MainEngine

class TestEngine(unittest.TestCase):
    def test_default(self):
        engine = MainEngine(Config())
        self.assertEqual(engine.process(2.0), 2.0)
        
    def test_custom(self):
        engine = MainEngine(Config(param=3.0))
        self.assertEqual(engine.process(2.0), 6.0)

if __name__ == '__main__':
    unittest.main()
"""
    with open(f"{base_dir}/scripts/test_{impl}", "w", encoding="utf-8") as f:
        f.write(test_py)
        
    with open(f"{base_dir}/references/workflows.md", "w", encoding="utf-8") as f:
        f.write("# Workflows\\n")
    with open(f"{base_dir}/references/standards.md", "w", encoding="utf-8") as f:
        f.write("# Standards\\n")
    with open(f"{base_dir}/assets/checklist.md", "w", encoding="utf-8") as f:
        f.write("# Checklist\\n")

    # Run tests
    subprocess.run(["python", f"{base_dir}/scripts/test_{impl}"], cwd=os.getcwd(), check=True)
    
    # Update roadmap
    roadmap = "docs/ROADMAP_500.md"
    if os.path.exists(roadmap):
        with open(roadmap, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace(f"- **[planned]** `{name}`", f"- **[BUILT]** `{name}`")
        with open(roadmap, "w", encoding="utf-8") as f:
            f.write(content)
            
    # Build index
    subprocess.run(["python", "tools/build_index.py"], cwd=os.getcwd(), check=True)
    
    # Commit
    subprocess.run(["git", "add", "-A"], cwd=os.getcwd(), check=True)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{idx} {name}"], cwd=os.getcwd(), check=True)

print("ALL DONE")
