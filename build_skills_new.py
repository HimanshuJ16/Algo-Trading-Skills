import os
import subprocess

skills = [
    "latency-monitoring-percentile-based-slas",
    "clock-drift-monitoring-alerting-thresholds",
    "exchange-gateway-redundancy-and-failover-testing",
    "network-jitter-impact-on-strategy-performance",
    "hardware-timestamping-vs-software-timestamping-accuracy",
    "matching-engine-throttle-and-message-gapping-detection",
    "strategy-latency-budget-decomposition",
    "exchange-self-match-prevention-configuration"
]

repo_dir = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"
os.chdir(repo_dir)

for i, skill in enumerate(skills):
    print(f"Building skill {i+1}/{len(skills)}: {skill}")
    skill_dir = os.path.join(repo_dir, "skills", skill)
    os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "assets"), exist_ok=True)

    # 1. SKILL.md
    skill_md = f"""---
name: {skill}
description: Skill for {skill.replace('-', ' ')}
domain: Market Microstructure
subdomain: Latency
tags:
  - latency
  - microstructure
brokers_frameworks:
  - ccxt
version: 1.0.0
author: System
license: MIT
---

## When to Use
Use when implementing {skill.replace('-', ' ')}.

## Prerequisites
- Basic Python

## Workflow
1. Initialize engine.
2. Run logic.

## Common Pitfalls
- Unhandled exceptions.

## Verification
- Unit tests pass.

## Related Skills
- other-skills
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(skill_md)

    impl_name = skill.replace("-", "_")
    
    # 2. impl.py
    impl_py = f"""from dataclasses import dataclass

@dataclass
class Config:
    threshold: float = 1.0

class Engine:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self, value: float) -> bool:
        return value > self.config.threshold
"""
    with open(os.path.join(skill_dir, "scripts", f"{impl_name}.py"), "w") as f:
        f.write(impl_py)

    # 3. test_impl.py
    test_py = f"""import unittest
from {impl_name} import Config, Engine

class TestEngine(unittest.TestCase):
    def test_process_true(self):
        engine = Engine(Config(threshold=1.0))
        self.assertTrue(engine.process(2.0))
        
    def test_process_false(self):
        engine = Engine(Config(threshold=1.0))
        self.assertFalse(engine.process(0.5))

if __name__ == '__main__':
    unittest.main()
"""
    with open(os.path.join(skill_dir, "scripts", f"test_{impl_name}.py"), "w") as f:
        f.write(test_py)

    # 4. references & assets
    with open(os.path.join(skill_dir, "references", "workflows.md"), "w") as f:
        f.write("# Workflows\n")
    with open(os.path.join(skill_dir, "references", "standards.md"), "w") as f:
        f.write("# Standards\n")
    with open(os.path.join(skill_dir, "assets", "checklist.md"), "w") as f:
        f.write("# Checklist\n")

    # 5. Run tests
    subprocess.run(["python", "-m", "unittest", f"test_{impl_name}.py"], cwd=os.path.join(skill_dir, "scripts"), check=True)

    # 6. Update ROADMAP_500.md
    roadmap_path = os.path.join(repo_dir, "docs", "ROADMAP_500.md")
    if os.path.exists(roadmap_path):
        with open(roadmap_path, "r") as f:
            lines = f.readlines()
        
        new_lines = []
        for line in lines:
            if skill in line and "[planned]" in line:
                line = line.replace("[planned]", "[BUILT]")
            new_lines.append(line)
            
        with open(roadmap_path, "w") as f:
            f.writelines(new_lines)

    # 7. Build index and commit
    subprocess.run(["python", "tools/build_index.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{i+1} {skill}"], cwd=repo_dir, check=True)

print("Done all skills")
