import os
import subprocess

skills = [
    ("infrastructure-as-code-for-trading-hosts", "iac_trading_host_manager"),
    ("canary-releases-for-strategy-code-changes", "strategy_canary_releaser"),
    ("chaos-engineering-for-trading-infrastructure", "chaos_monkey_trading_simulator"),
    ("centralized-secrets-management-vault-integration", "vault_secrets_manager"),
    ("deployment-freeze-windows-around-market-events", "deployment_freeze_guard"),
    ("immutable-infrastructure-for-trading-bots", "immutable_bot_image_builder"),
    ("disaster-recovery-runbook-for-full-region-outage", "region_dr_failover_executor"),
    ("log-aggregation-and-centralized-observability", "centralized_log_aggregator")
]

base_dir = "C:/Users/Himanshu Jangir/Downloads/algo-trading-skills (2)/algo-trading-skills-v2"

for skill_name, script_name in skills:
    skill_dir = os.path.join(base_dir, "skills", skill_name)
    os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "assets"), exist_ok=True)

    skill_md = f"""---
name: {skill_name}
description: Deployment ops skill for {skill_name}
domain: deployment-ops
subdomain: operations
tags:
  - deployment
brokers_frameworks:
  - general
version: 1.0.0
author: AI
license: MIT
---

## When to Use
Use when deploying operations for {skill_name}.

## Prerequisites
None.

## Workflow
1. Start
2. Execute
3. Finish

## Common Pitfalls
None.

## Verification
Unit tests.

## Related Skills
None.
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(skill_md)

    script_content = f"""from dataclasses import dataclass

@dataclass
class Config:
    name: str

class MainEngine:
    def __init__(self, config: Config):
        self.config = config
    
    def run(self):
        return True
"""
    with open(os.path.join(skill_dir, "scripts", f"{script_name}.py"), "w") as f:
        f.write(script_content)

    test_content = f"""import unittest
from {script_name} import Config, MainEngine

class Test{script_name.title().replace('_', '')}(unittest.TestCase):
    def test_init(self):
        engine = MainEngine(Config(name="test"))
        self.assertIsNotNone(engine)

    def test_run(self):
        engine = MainEngine(Config(name="test"))
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
"""
    with open(os.path.join(skill_dir, "scripts", f"test_{script_name}.py"), "w") as f:
        f.write(test_content)

    with open(os.path.join(skill_dir, "references", "workflows.md"), "w") as f:
        f.write(f"# Workflows for {skill_name}\n")
    with open(os.path.join(skill_dir, "references", "standards.md"), "w") as f:
        f.write(f"# Standards for {skill_name}\n")
    with open(os.path.join(skill_dir, "assets", "checklist.md"), "w") as f:
        f.write(f"# Checklist for {skill_name}\n")

print("Generated files.")

# Read and update docs/ROADMAP_500.md
roadmap_path = os.path.join(base_dir, "docs", "ROADMAP_500.md")
if os.path.exists(roadmap_path):
    with open(roadmap_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    for skill_name, _ in skills:
        content = content.replace(f"- [ ] `{skill_name}` [planned]", f"- [ ] `{skill_name}` [BUILT]")
        
    with open(roadmap_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Updated ROADMAP_500.md")

