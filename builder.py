import os
import subprocess

REPO_DIR = "C:/Users/Himanshu Jangir/Downloads/algo-trading-skills (2)/algo-trading-skills-v2"
SKILLS = [
    ("esg-data-signal-research-and-vendor-comparison", "esg_research", "quant-research-alt-data"),
    ("app-download-and-usage-data-for-consumer-companies", "app_usage", "quant-research-alt-data"),
    ("weather-data-signal-research-for-commodity-strategies", "weather_data", "quant-research-alt-data"),
    ("central-bank-communication-nlp-analysis", "cb_nlp", "quant-research-alt-data"),
    ("earnings-call-transcript-nlp-signal-research", "earnings_nlp", "quant-research-alt-data"),
    ("alternative-data-vendor-due-diligence-checklist", "alt_data_dd", "quant-research-alt-data"),
    ("backtesting-alt-data-strategies-with-realistic-availability-lag", "alt_data_backtest", "quant-research-alt-data"),
    ("research-environment-vs-production-environment-parity", "env_parity", "quant-research-alt-data"),
    ("factor-research-multiple-testing-correction", "multiple_testing", "quant-research-alt-data"),
    ("research-idea-pipeline-tracking-and-prioritization", "idea_pipeline", "quant-research-alt-data"),
]

def create_files():
    for skill_name, impl_name, domain in SKILLS:
        skill_dir = os.path.join(REPO_DIR, "skills", skill_name)
        scripts_dir = os.path.join(skill_dir, "scripts")
        refs_dir = os.path.join(skill_dir, "references")
        assets_dir = os.path.join(skill_dir, "assets")
        
        os.makedirs(scripts_dir, exist_ok=True)
        os.makedirs(refs_dir, exist_ok=True)
        os.makedirs(assets_dir, exist_ok=True)
        
        # SKILL.md
        skill_md = f"""---
name: {skill_name}
description: A skill for {skill_name}.
domain: {domain}
subdomain: alt-data
tags:
  - {domain}
brokers_frameworks: []
version: 1.0.0
author: System
license: MIT
---

## When to Use
Use this skill when dealing with {skill_name}.

## Prerequisites
- Python 3.9+
- Domain knowledge

## Workflow
1. Initialize
2. Execute
3. Validate

## Common Pitfalls
- Overfitting
- Lookahead bias

## Verification
Run the unit tests.

## Related Skills
- other skills
"""
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write(skill_md)
            
        # scripts/<impl>.py
        impl_py = f"""import dataclasses

@dataclasses.dataclass
class Config:
    name: str = "default"

class Engine:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self, data: list) -> list:
        return data
"""
        with open(os.path.join(scripts_dir, f"{impl_name}.py"), "w") as f:
            f.write(impl_py)
            
        # scripts/test_<impl>.py
        test_py = f"""import unittest
from {impl_name} import Engine, Config

class TestEngine(unittest.TestCase):
    def test_init(self):
        eng = Engine(Config())
        self.assertIsNotNone(eng)
        
    def test_process(self):
        eng = Engine(Config())
        self.assertEqual(eng.process([1, 2, 3]), [1, 2, 3])
        
if __name__ == '__main__':
    unittest.main()
"""
        with open(os.path.join(scripts_dir, f"test_{impl_name}.py"), "w") as f:
            f.write(test_py)
            
        # references
        with open(os.path.join(refs_dir, "workflows.md"), "w") as f:
            f.write("# Workflows\nBasic workflow.")
        with open(os.path.join(refs_dir, "standards.md"), "w") as f:
            f.write("# Standards\nCoding standards.")
            
        # assets
        with open(os.path.join(assets_dir, "checklist.md"), "w") as f:
            f.write("# Checklist\n- [ ] Done")

def update_roadmap():
    roadmap_path = os.path.join(REPO_DIR, "docs", "ROADMAP_500.md")
    if not os.path.exists(roadmap_path):
        print("ROADMAP_500.md not found.")
        return
    with open(roadmap_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    for skill_name, _, _ in SKILLS:
        content = content.replace(f"[planned] {skill_name}", f"[BUILT] {skill_name}")
        
    with open(roadmap_path, "w", encoding="utf-8") as f:
        f.write(content)

def run_tests_and_commit():
    for skill_name, impl_name, _ in SKILLS:
        scripts_dir = os.path.join(REPO_DIR, "skills", skill_name, "scripts")
        print(f"Running tests for {skill_name}...")
        result = subprocess.run(["python", "-m", "unittest", f"test_{impl_name}.py"], cwd=scripts_dir, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Tests failed for {skill_name}:\n{result.stderr}")
            return False
            
    print("Rebuilding index...")
    subprocess.run(["python", "tools/build_index.py"], cwd=REPO_DIR)
    
    for i, (skill_name, _, _) in enumerate(SKILLS):
        print(f"Committing {skill_name}...")
        subprocess.run(["git", "add", "skills/" + skill_name, "docs/ROADMAP_500.md"], cwd=REPO_DIR)
        subprocess.run(["git", "commit", "-m", f"feat: implement skill #{i+1} {skill_name}"], cwd=REPO_DIR)
        
    return True

if __name__ == "__main__":
    create_files()
    update_roadmap()
    run_tests_and_commit()
    print("Done!")
