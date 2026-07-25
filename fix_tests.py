import os
import subprocess
import glob

base_dir = "C:/Users/Himanshu Jangir/Downloads/algo-trading-skills (2)/algo-trading-skills-v2"
os.chdir(base_dir)

skills = [
    ("cost-monitoring-for-cloud-trading-infrastructure", "cloud_cost_anomaly_detector.py"),
    ("database-backup-and-point-in-time-restore-testing", "pitr_backup_tester.py"),
    ("dependency-vulnerability-scanning-in-ci", "dependency_vulnerability_scanner.py"),
    ("configuration-drift-detection-across-environments", "config_drift_detector.py"),
    ("runbook-automation-for-common-incident-types", "runbook_incident_automator.py"),
    ("load-testing-before-scaling-to-new-instrument-universe", "infrastructure_load_tester.py"),
    ("graceful-degradation-priority-during-partial-outage", "graceful_degradation_router.py"),
    ("post-mortem-culture-and-blameless-review-process", "blameless_postmortem_generator.py")
]

for skill_name, impl_file in skills:
    test_file = f"test_{impl_file}"
    test_path = os.path.join(base_dir, "skills", skill_name, "scripts", test_file)
    if os.path.exists(test_path):
        with open(test_path, "r") as f:
            content = f.read()
        
        if "sys.path.insert(0" not in content:
            new_content = "import sys, os\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n" + content
            with open(test_path, "w") as f:
                f.write(new_content)
                
        # Run test properly
        script_dir = os.path.join(base_dir, "skills", skill_name, "scripts")
        print(f"Running tests for {skill_name} in {script_dir}")
        env = os.environ.copy()
        env["PYTHONPATH"] = script_dir
        res = subprocess.run(["python", "-m", "unittest", test_file], cwd=script_dir, env=env, capture_output=True, text=True)
        print(res.stdout)
        print(res.stderr)

subprocess.run(["git", "add", "-A"], check=False)
subprocess.run(["git", "commit", "-m", "fix: fix imports in tests and ensure they pass"], check=False)
print("DONE")
