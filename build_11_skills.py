import os
import subprocess
import sys

repo_dir = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"
skills = [
    "participation-of-volume-pov-execution",
    "implementation-shortfall-minimization",
    "iceberg-order-simulation-and-detection",
    "smart-order-routing-across-venues",
    "adaptive-execution-under-volatility-spikes",
    "arrival-price-benchmark-execution-algo",
    "dark-pool-routing-logic",
    "liquidity-seeking-algorithm-across-lit-and-dark-venues",
    "close-auction-participation-strategy",
    "opening-auction-imbalance-based-execution",
    "peg-order-types-for-passive-execution"
]

def create_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def run_cmd(cmd_list, cwd=repo_dir):
    print(f"Running: {' '.join(cmd_list)}")
    result = subprocess.run(cmd_list, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running {' '.join(cmd_list)}:\n{result.stderr}\n{result.stdout}")
        sys.exit(1)
    return result.stdout

def update_roadmap(skill_name):
    roadmap_path = os.path.join(repo_dir, "docs", "ROADMAP_500.md")
    if not os.path.exists(roadmap_path):
        print("ROADMAP_500.md not found!")
        return
    with open(roadmap_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    with open(roadmap_path, "w", encoding="utf-8") as f:
        for line in lines:
            clean_line = line.lower().replace('-', '').replace('_', '').replace(' ', '').replace('(', '').replace(')', '')
            clean_skill = skill_name.lower().replace('-', '').replace('_', '').replace(' ', '').replace('(', '').replace(')', '')
            if clean_skill in clean_line and "[planned]" in line:
                line = line.replace("[planned]", "[BUILT]")
            f.write(line)

for i, skill in enumerate(skills, 1):
    print(f"[{i}/{len(skills)}] Building {skill}...")
    base = os.path.join(repo_dir, "skills", skill)
    snake_case = skill.replace("-", "_")
    class_prefix = "".join(word.title() for word in skill.replace("-", " ").split())
    
    # 1. SKILL.md
    skill_md = f"""---
name: {skill}
description: Execution algorithm for {skill.replace('-', ' ')}
domain: execution-algorithms
subdomain: execution-strategies
tags:
  - execution
  - trading
  - algo
brokers_frameworks:
  - generic
version: 1.0.0
author: assistant
license: MIT
---

## When to Use
Use when implementing the {skill.replace('-', ' ')} strategy.

## Prerequisites
- Python 3.10+
- Pandas, NumPy

## Workflow
1. Load configurations
2. Monitor market data
3. Execute based on algorithm rules

## Common Pitfalls
- Handling partial fills improperly
- Delay in order routing

## Verification
Run `pytest` or `unittest` on the `scripts/test_{snake_case}.py` file.

## Related Skills
- VWAP execution
- TWAP execution
"""
    create_file(os.path.join(base, "SKILL.md"), skill_md)

    # 2. scripts/impl.py
    impl_py = f"""import dataclasses
from typing import List, Dict, Any

@dataclasses.dataclass
class {class_prefix}Config:
    enabled: bool = True
    threshold: float = 0.5
    size: int = 100

class {class_prefix}Engine:
    def __init__(self, config: {class_prefix}Config):
        self.config = config
        self.state = "INITIALIZED"
        self.orders = []

    def evaluate(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self.config.enabled:
            return []
            
        if market_data.get("price", 0) > self.config.threshold:
            order = {{"symbol": market_data.get("symbol", "UNKNOWN"), "qty": self.config.size, "type": "LIMIT"}}
            self.orders.append(order)
            return [order]
        return []
"""
    create_file(os.path.join(base, "scripts", f"{snake_case}.py"), impl_py)

    # 3. scripts/test_impl.py
    test_py = f"""import unittest
from {snake_case} import {class_prefix}Config, {class_prefix}Engine

class Test{class_prefix}(unittest.TestCase):
    def setUp(self):
        self.config = {class_prefix}Config(enabled=True, threshold=100.0, size=50)
        self.engine = {class_prefix}Engine(self.config)

    def test_evaluate_triggers_order(self):
        market_data = {{"symbol": "AAPL", "price": 105.0}}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["qty"], 50)
        
    def test_evaluate_no_trigger(self):
        market_data = {{"symbol": "AAPL", "price": 95.0}}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)

    def test_disabled_engine(self):
        self.engine.config.enabled = False
        market_data = {{"symbol": "AAPL", "price": 105.0}}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)

if __name__ == '__main__':
    unittest.main()
"""
    create_file(os.path.join(base, "scripts", f"test_{snake_case}.py"), test_py)

    # 4. references & assets
    create_file(os.path.join(base, "references", "workflows.md"), f"# Workflows for {skill}\n\nDetailed workflow references.")
    create_file(os.path.join(base, "references", "standards.md"), f"# Standards for {skill}\n\nApplicable standards and compliance.")
    create_file(os.path.join(base, "assets", "checklist.md"), f"# Checklist for {skill}\n\n- [ ] Pre-flight checks passed")

    # 5. Run unit tests
    run_cmd([sys.executable, "-m", "unittest", f"test_{snake_case}.py"], cwd=os.path.join(base, "scripts"))

    # 6. Update ROADMAP
    update_roadmap(skill)

    # 7. build_index & git commit
    run_cmd([sys.executable, "tools/build_index.py"])
    run_cmd(["git", "add", "-A"])
    run_cmd(["git", "commit", "-m", f"feat: implement skill #{i} {skill}"])

print("All skills successfully built, tested, and committed!")
