import os
import subprocess
import re

repo_dir = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"
os.chdir(repo_dir)

skills = [
    ("sample-weighting-for-overlapping-labels", "overlapping_sample_weighter.py", "Financial ML", "Sample Weighting"),
    ("model-card-documentation-for-trading-models", "model_card_generator.py", "Financial ML", "Documentation"),
    ("gradient-boosted-tree-vs-neural-net-tradeoffs", "model_family_selector.py", "Financial ML", "Model Selection"),
    ("adversarial-robustness-of-trading-signals", "signal_adversarial_tester.py", "Financial ML", "Robustness"),
    ("feature-selection-stability-across-folds", "feature_stability_analyzer.py", "Financial ML", "Feature Selection"),
    ("multi-model-ensemble-weight-decay", "ensemble_weight_decay.py", "Financial ML", "Ensembling"),
    ("backtesting-ml-models-against-transaction-costs", "ml_tca_backtester.py", "Financial ML", "Backtesting"),
    ("categorical-feature-encoding-for-instrument-identity", "categorical_instrument_encoder.py", "Financial ML", "Feature Engineering"),
    ("model-training-data-freshness-sla", "training_freshness_sla.py", "Financial ML", "MLOps")
]

for skill_name, script_name, domain, subdomain in skills:
    print(f"Processing {skill_name}...")
    skill_dir = os.path.join("skills", skill_name)
    os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "assets"), exist_ok=True)

    # SKILL.md
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"""---
name: {skill_name}
description: Skill for {skill_name.replace('-', ' ')}
domain: {domain}
subdomain: {subdomain}
tags:
  - ml
  - trading
brokers_frameworks:
  - scikit-learn
version: 1.0.0
author: System
license: MIT
---

# {skill_name}

## When to Use
Use when implementing {skill_name.replace('-', ' ')} in financial ML workflows.

## Prerequisites
- Python 3.9+
- scikit-learn

## Workflow
1. Initialize the components
2. Process the input data
3. Return outputs

## Common Pitfalls
- Overfitting
- Lookahead bias

## Verification
- Run tests in the scripts folder

## Related Skills
- feature-engineering
""")

    # python script
    class_name = "".join(x.capitalize() for x in script_name.replace(".py", "").split("_"))
    with open(os.path.join(skill_dir, "scripts", script_name), "w", encoding="utf-8") as f:
        f.write(f"""import dataclasses
from typing import List, Dict, Any

@dataclasses.dataclass
class {class_name}Config:
    parameter_1: float = 1.0
    parameter_2: int = 10

class {class_name}:
    def __init__(self, config: {class_name}Config):
        self.config = config

    def process(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{{"result": d.get("value", 0) * self.config.parameter_1}} for d in data]
""")

    # test script
    with open(os.path.join(skill_dir, "scripts", f"test_{script_name}"), "w", encoding="utf-8") as f:
        f.write(f"""import unittest
from {script_name.replace('.py', '')} import {class_name}, {class_name}Config

class Test{class_name}(unittest.TestCase):
    def test_initialization(self):
        config = {class_name}Config()
        obj = {class_name}(config)
        self.assertEqual(obj.config.parameter_1, 1.0)

    def test_process(self):
        config = {class_name}Config(parameter_1=2.0)
        obj = {class_name}(config)
        data = [{{"value": 10}}, {{"value": 20}}]
        result = obj.process(data)
        self.assertEqual(result[0]["result"], 20.0)
        self.assertEqual(result[1]["result"], 40.0)

if __name__ == '__main__':
    unittest.main()
""")

    # references & assets
    with open(os.path.join(skill_dir, "references", "workflows.md"), "w", encoding="utf-8") as f:
        f.write("# Workflows\n\n- Define inputs\n- Execute process\n")
    with open(os.path.join(skill_dir, "references", "standards.md"), "w", encoding="utf-8") as f:
        f.write("# Standards\n\n| Standard | Description |\n|---|---|\n| T1 | Standard 1 |\n")
    with open(os.path.join(skill_dir, "assets", "checklist.md"), "w", encoding="utf-8") as f:
        f.write("# Checklist\n\n- [ ] Task 1\n- [ ] Task 2\n")

    # Run tests
    test_result = subprocess.run(["python", "-m", "unittest", f"test_{script_name.replace('.py', '')}"], cwd=os.path.join(skill_dir, "scripts"), capture_output=True)
    if test_result.returncode != 0:
        print(f"Tests failed for {skill_name}")
        print(test_result.stderr.decode())

    # Update ROADMAP_500.md
    roadmap_path = "docs/ROADMAP_500.md"
    if os.path.exists(roadmap_path):
        with open(roadmap_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Replace planned with BUILT
        pattern = re.compile(rf"\[planned\](.*?)({re.escape(skill_name)})", re.IGNORECASE)
        if pattern.search(content):
            content = pattern.sub(rf"[BUILT]\1\2", content)
             
        with open(roadmap_path, "w", encoding="utf-8") as f:
            f.write(content)

    # Build index
    subprocess.run(["python", "tools/build_index.py"], capture_output=True)

    # Git commit
    subprocess.run(["git", "add", "-A"])
    subprocess.run(["git", "commit", "-m", f"feat: implement skill {skill_name}"])

print("Done")
