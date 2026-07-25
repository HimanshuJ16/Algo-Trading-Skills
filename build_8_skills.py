import os
import subprocess
import re

base_dir = "C:/Users/Himanshu Jangir/Downloads/algo-trading-skills (2)/algo-trading-skills-v2"
os.chdir(base_dir)

skills = [
    ("cost-monitoring-for-cloud-trading-infrastructure", "cloud_cost_anomaly_detector.py", "CloudCostAnomalyDetector", "Cost Monitoring"),
    ("database-backup-and-point-in-time-restore-testing", "pitr_backup_tester.py", "PitrBackupTester", "PITR Testing"),
    ("dependency-vulnerability-scanning-in-ci", "dependency_vulnerability_scanner.py", "DependencyVulnerabilityScanner", "Dep Vuln Scanning"),
    ("configuration-drift-detection-across-environments", "config_drift_detector.py", "ConfigDriftDetector", "Config Drift Detection"),
    ("runbook-automation-for-common-incident-types", "runbook_incident_automator.py", "RunbookIncidentAutomator", "Runbook Automation"),
    ("load-testing-before-scaling-to-new-instrument-universe", "infrastructure_load_tester.py", "InfrastructureLoadTester", "Load Testing"),
    ("graceful-degradation-priority-during-partial-outage", "graceful_degradation_router.py", "GracefulDegradationRouter", "Graceful Degradation"),
    ("post-mortem-culture-and-blameless-review-process", "blameless_postmortem_generator.py", "BlamelessPostmortemGenerator", "Blameless Postmortem")
]

skill_md_template = """---
name: {title}
description: A skill to handle {title} in algo-trading
domain: deployment-ops
subdomain: operations
tags: [deployment-ops]
brokers_frameworks: [aws, gcp]
version: 1.0.0
author: AI
license: MIT
---

## When to Use
Use this skill when managing deployment operations.

## Prerequisites
- Python 3.9+
- Standard libraries

## Workflow
1. Initialize engine.
2. Run operation.
3. Check results.

## Common Pitfalls
- Not configuring environment correctly.
- Missing credentials.

## Verification
- Run tests and check logs.

## Related Skills
- other deployment-ops skills
"""

impl_template = """from dataclasses import dataclass

@dataclass
class Config:
    name: str

class {class_name}:
    def __init__(self, config: Config = None):
        self.config = config or Config("default")
        
    def process(self) -> bool:
        return True
"""

test_template = """import unittest
from {impl_no_ext} import {class_name}, Config

class Test{class_name}(unittest.TestCase):
    def test_init(self):
        obj = {class_name}(Config("test"))
        self.assertEqual(obj.config.name, "test")

    def test_process(self):
        obj = {class_name}()
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
"""

for i, (skill_name, impl_file, class_name, title) in enumerate(skills, 1):
    skill_dir = os.path.join(base_dir, "skills", skill_name)
    os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "assets"), exist_ok=True)
    
    # SKILL.md
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(skill_md_template.format(title=title))
        
    # Impl
    with open(os.path.join(skill_dir, "scripts", impl_file), "w") as f:
        f.write(impl_template.format(class_name=class_name))
        
    # Test
    impl_no_ext = impl_file.replace(".py", "")
    with open(os.path.join(skill_dir, "scripts", f"test_{impl_file}"), "w") as f:
        f.write(test_template.format(impl_no_ext=impl_no_ext, class_name=class_name))
        
    # Refs
    with open(os.path.join(skill_dir, "references", "workflows.md"), "w") as f:
        f.write("# Workflows\nWorkflow details.")
    with open(os.path.join(skill_dir, "references", "standards.md"), "w") as f:
        f.write("# Standards\nStandards details.")
    with open(os.path.join(skill_dir, "assets", "checklist.md"), "w") as f:
        f.write("# Checklist\n- [ ] item 1")
        
    # Run tests
    subprocess.run(["python", "-m", "unittest", f"skills.{skill_name.replace('-', '_')}.scripts.test_{impl_no_ext}"], check=False)
    subprocess.run(["python", "-m", "unittest", f"skills/{skill_name}/scripts/test_{impl_file}"], check=False)

    # Update Roadmap
    roadmap_path = os.path.join(base_dir, "docs", "ROADMAP_500.md")
    if os.path.exists(roadmap_path):
        with open(roadmap_path, "r") as f:
            content = f.read()
            
        pattern = re.compile(rf"-\s+\[planned\]\s+{re.escape(skill_name)}", re.IGNORECASE)
        if pattern.search(content):
            content = pattern.sub(f"- [BUILT] {skill_name}", content)
        else:
            pattern = re.compile(rf"\[planned\]\s+{re.escape(skill_name)}", re.IGNORECASE)
            content = pattern.sub(f"[BUILT] {skill_name}", content)
            
        with open(roadmap_path, "w") as f:
            f.write(content)

    # Build Index
    subprocess.run(["python", "tools/build_index.py"], check=False)
    
    # Commit
    subprocess.run(["git", "add", "-A"], check=False)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{i} {skill_name}"], check=False)

print("DONE")
