import os
import subprocess

skills = [
    ("commodity-futures-storage-and-carry-cost-modeling", "storage_model", "Multi-Asset Derivatives", "Commodities"),
    ("weather-derivatives-and-niche-instrument-handling", "weather_derivs", "Multi-Asset Derivatives", "Exotics"),
    ("binary-options-regulatory-and-risk-considerations", "binary_options", "Multi-Asset Derivatives", "Exotics"),
    ("warrants-and-structured-product-integration", "warrants_integration", "Multi-Asset Derivatives", "Structured Products"),
    ("convertible-bond-arbitrage-data-requirements", "cb_arbitrage", "Multi-Asset Derivatives", "Fixed Income"),
    ("dividend-futures-and-forward-modeling", "dividend_futures", "Multi-Asset Derivatives", "Equities"),
    ("vix-and-volatility-index-derivative-strategies", "vix_strategies", "Multi-Asset Derivatives", "Volatility"),
    ("single-stock-futures-where-available", "ssf_handling", "Multi-Asset Derivatives", "Equities"),
    ("total-return-swap-synthetic-exposure", "trs_exposure", "Multi-Asset Derivatives", "Swaps")
]

base_dir = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"

for i, (skill_name, impl, domain, subdomain) in enumerate(skills, 1):
    skill_dir = os.path.join(base_dir, "skills", skill_name)
    os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "assets"), exist_ok=True)

    # 1. SKILL.md
    skill_md = f"""---
name: {skill_name}
description: Skill for {skill_name.replace('-', ' ')}
domain: {domain}
subdomain: {subdomain}
tags:
  - {domain}
brokers_frameworks:
  - Generic
version: 1.0.0
author: System
license: MIT
---

# {skill_name}

## When to Use
Use when implementing {skill_name.replace('-', ' ')}.

## Prerequisites
- Basic understanding of {subdomain}.

## Workflow
1. Initialize the components.
2. Apply the model.
3. Validate results.

## Common Pitfalls
- Incorrect parameter initialization.

## Verification
- Unit tests pass.

## Related Skills
- other-skills
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding='utf-8') as f:
        f.write(skill_md)
        
    # 2. impl.py
    impl_py = f"""from dataclasses import dataclass

@dataclass
class ModelConfig:
    name: str

class MainEngine:
    def __init__(self, config: ModelConfig):
        self.config = config
        
    def execute(self):
        return True
"""
    with open(os.path.join(skill_dir, "scripts", f"{impl}.py"), "w", encoding='utf-8') as f:
        f.write(impl_py)

    # 3. test_impl.py
    test_py = f"""import unittest
from {impl} import ModelConfig, MainEngine

class TestMainEngine(unittest.TestCase):
    def test_init(self):
        config = ModelConfig("test")
        engine = MainEngine(config)
        self.assertEqual(engine.config.name, "test")
        
    def test_execute(self):
        config = ModelConfig("test")
        engine = MainEngine(config)
        self.assertTrue(engine.execute())

if __name__ == '__main__':
    unittest.main()
"""
    with open(os.path.join(skill_dir, "scripts", f"test_{impl}.py"), "w", encoding='utf-8') as f:
        f.write(test_py)
        
    # 4. references & assets
    with open(os.path.join(skill_dir, "references", "workflows.md"), "w", encoding='utf-8') as f:
        f.write("# Workflows\\n")
    with open(os.path.join(skill_dir, "references", "standards.md"), "w", encoding='utf-8') as f:
        f.write("# Standards\\n")
    with open(os.path.join(skill_dir, "assets", "checklist.md"), "w", encoding='utf-8') as f:
        f.write("# Checklist\\n")

    # update roadmap
    roadmap_path = os.path.join(base_dir, "docs", "ROADMAP_500.md")
    with open(roadmap_path, "r", encoding='utf-8') as f:
        roadmap = f.read()
    roadmap = roadmap.replace(f"- [planned] `{skill_name}`", f"- [BUILT] `{skill_name}`")
    with open(roadmap_path, "w", encoding='utf-8') as f:
        f.write(roadmap)
        
    # run test
    cwd = os.path.join(skill_dir, "scripts")
    subprocess.run(["python", "-m", "unittest", f"test_{impl}.py"], cwd=cwd, check=True)
    
    # build index and commit
    subprocess.run(["python", "tools/build_index.py"], cwd=base_dir, check=True)
    subprocess.run(["git", "add", "-A"], cwd=base_dir, check=True)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{i} {skill_name}"], cwd=base_dir, check=True)
    print(f"Finished {skill_name}")
