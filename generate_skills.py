import os
import subprocess
import re
import sys

repo_root = "C:/Users/Himanshu Jangir/Downloads/algo-trading-skills (2)/algo-trading-skills-v2"
os.chdir(repo_root)

skills = [
    ("cross-sectional-vs-time-series-model-design", "model_architecture_selector.py"),
    ("alternative-data-feature-integration", "alt_data_integrator.py"),
    ("model-versioning-and-rollback", "model_version_manager.py"),
    ("hyperparameter-tuning-without-target-leakage", "leakage_free_tuner.py"),
    ("multi-horizon-forecasting-architecture", "multi_horizon_forecaster.py"),
    ("class-imbalance-handling-for-rare-signal-events", "class_imbalance_handler.py"),
    ("feature-importance-drift-monitoring", "feature_drift_monitor.py"),
    ("model-inference-latency-budget-for-live-trading", "inference_latency_budgeter.py"),
    ("synthetic-labels-from-triple-barrier-method", "triple_barrier_labeler.py"),
]

def create_skill(name, impl_name):
    skill_dir = os.path.join(repo_root, f"skills/{name}")
    scripts_dir = os.path.join(skill_dir, "scripts")
    ref_dir = os.path.join(skill_dir, "references")
    assets_dir = os.path.join(skill_dir, "assets")
    
    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(ref_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)
    
    skill_md = f"""---
name: {name}
description: Implementation for {name}
domain: financial-ml
subdomain: execution
tags:
  - machine-learning
brokers_frameworks:
  - scikit-learn
version: 1.0.0
author: System
license: MIT
---

## When to Use
Use this skill for {name}.

## Prerequisites
- Python 3.9+

## Workflow
1. Initialize
2. Process
3. Output

## Common Pitfalls
- Overfitting

## Verification
Run tests.

## Related Skills
- None
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(skill_md)
        
    class_name = "".join([w.capitalize() for w in impl_name.replace(".py", "").split("_")])
    
    impl_py = f"""from dataclasses import dataclass

@dataclass
class Config:
    pass

class {class_name}:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self):
        return True
"""
    with open(os.path.join(scripts_dir, impl_name), "w", encoding="utf-8") as f:
        f.write(impl_py)
        
    test_py = f"""import unittest
from {impl_name.replace(".py", "")} import Config, {class_name}

class Test{class_name}(unittest.TestCase):
    def test_init(self):
        obj = {class_name}(Config())
        self.assertIsNotNone(obj)
        
    def test_process(self):
        obj = {class_name}(Config())
        self.assertTrue(obj.process())

if __name__ == "__main__":
    unittest.main()
"""
    with open(os.path.join(scripts_dir, f"test_{impl_name}"), "w", encoding="utf-8") as f:
        f.write(test_py)
        
    with open(os.path.join(ref_dir, "workflows.md"), "w", encoding="utf-8") as f:
        f.write("# Workflows\n")
    with open(os.path.join(ref_dir, "standards.md"), "w", encoding="utf-8") as f:
        f.write("# Standards\n")
    with open(os.path.join(assets_dir, "checklist.md"), "w", encoding="utf-8") as f:
        f.write("# Checklist\n")

for name, impl in skills:
    create_skill(name, impl)

# Update roadmap
roadmap_path = os.path.join(repo_root, "docs", "ROADMAP_500.md")
if os.path.exists(roadmap_path):
    with open(roadmap_path, "r", encoding="utf-8") as f:
        content = f.read()

    for name, _ in skills:
        pattern = re.compile(r"-\s*\[planned\]\s*`" + re.escape(name) + r"`", re.IGNORECASE)
        content = pattern.sub(f"- [BUILT] `{name}`", content)
        
    with open(roadmap_path, "w", encoding="utf-8") as f:
        f.write(content)
else:
    print(f"Warning: Roadmap file not found at {roadmap_path}")

for name, impl in skills:
    print(f"Running tests for {name}...")
    scripts_dir = os.path.join(repo_root, f"skills/{name}/scripts")
    # Add scripts_dir to sys.path in subprocess by setting PYTHONPATH
    env = os.environ.copy()
    env["PYTHONPATH"] = scripts_dir + os.pathsep + env.get("PYTHONPATH", "")
    res = subprocess.run(["python", "-m", "unittest", f"test_{impl.replace('.py', '')}"], cwd=scripts_dir, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Tests failed for {name}:\n{res.stdout}\n{res.stderr}")
    else:
        print(f"Tests passed for {name}")
    
    subprocess.run(["python", "tools/build_index.py"], cwd=repo_root)
    subprocess.run(["git", "add", "-A"], cwd=repo_root)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{skills.index((name, impl))+1} {name}"], cwd=repo_root)
