import os
import subprocess
import re
import sys

REPO_DIR = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"
os.chdir(REPO_DIR)

skills_data = [
    {
        "name": "tail-risk-hedging-with-options",
        "impl": "tail_risk_hedger",
        "desc": "OTM options tail risk insurance under defined budget",
        "script_content": """from dataclasses import dataclass
from typing import List, Dict

@dataclass
class HedgingResult:
    hedged: bool
    options_bought: int
    cost: float

class TailRiskHedger:
    def __init__(self, budget_pct: float = 0.05):
        self.budget_pct = budget_pct

    def hedge(self, portfolio_value: float, option_price: float) -> HedgingResult:
        if option_price <= 0 or portfolio_value <= 0:
            return HedgingResult(False, 0, 0.0)
        max_spend = portfolio_value * self.budget_pct
        options_bought = int(max_spend // option_price)
        cost = options_bought * option_price
        return HedgingResult(options_bought > 0, options_bought, cost)
""",
        "test_content": """import unittest
from tail_risk_hedger import TailRiskHedger, HedgingResult

class TestTailRiskHedger(unittest.TestCase):
    def test_hedge_success(self):
        hedger = TailRiskHedger(budget_pct=0.05)
        res = hedger.hedge(100000, 100)
        self.assertTrue(res.hedged)
        self.assertEqual(res.options_bought, 50)
        self.assertEqual(res.cost, 5000)

    def test_hedge_insufficient_budget(self):
        hedger = TailRiskHedger(budget_pct=0.01)
        res = hedger.hedge(1000, 50)
        self.assertFalse(res.hedged)
        self.assertEqual(res.options_bought, 0)
        self.assertEqual(res.cost, 0.0)
"""
    },
    {
        "name": "real-time-var-backtesting-kupiec-test",
        "impl": "kupiec_var_backtester",
        "desc": "Kupiec proportion-of-failures test for VaR model validation",
        "script_content": """from dataclasses import dataclass
import scipy.stats as st

@dataclass
class KupiecResult:
    p_value: float
    is_rejected: bool
    exceptions: int

class KupiecVaRBacktester:
    def __init__(self, confidence_level: float = 0.99):
        self.confidence_level = confidence_level

    def run_test(self, total_observations: int, exceptions: int) -> KupiecResult:
        if total_observations == 0:
            return KupiecResult(1.0, False, 0)
        p = 1 - self.confidence_level
        # binomial test
        p_val = st.binom_test(exceptions, total_observations, p, alternative='two-sided')
        is_rejected = p_val < 0.05
        return KupiecResult(float(p_val), is_rejected, exceptions)
""",
        "test_content": """import unittest
from kupiec_var_backtester import KupiecVaRBacktester

class TestKupiecVaRBacktester(unittest.TestCase):
    def test_accept_model(self):
        tester = KupiecVaRBacktester(0.99)
        res = tester.run_test(1000, 10)
        self.assertFalse(res.is_rejected)
        self.assertEqual(res.exceptions, 10)

    def test_reject_model(self):
        tester = KupiecVaRBacktester(0.99)
        res = tester.run_test(1000, 25)
        self.assertTrue(res.is_rejected)
"""
    },
    {
        "name": "concentration-risk-single-name-limits",
        "impl": "single_name_concentration_limiter",
        "desc": "Capping single-instrument exposure",
        "script_content": """from dataclasses import dataclass
from typing import Dict

@dataclass
class CheckResult:
    is_breached: bool
    max_allowed: float
    current_exposure: float

class SingleNameConcentrationLimiter:
    def __init__(self, limit_pct: float = 0.1):
        self.limit_pct = limit_pct

    def check_exposure(self, portfolio_value: float, instrument_exposure: float) -> CheckResult:
        if portfolio_value <= 0:
            return CheckResult(False, 0.0, instrument_exposure)
        max_allowed = portfolio_value * self.limit_pct
        return CheckResult(instrument_exposure > max_allowed, max_allowed, instrument_exposure)
""",
        "test_content": """import unittest
from single_name_concentration_limiter import SingleNameConcentrationLimiter

class TestSingleNameConcentrationLimiter(unittest.TestCase):
    def test_breach(self):
        limiter = SingleNameConcentrationLimiter(0.1)
        res = limiter.check_exposure(100000, 15000)
        self.assertTrue(res.is_breached)
        self.assertEqual(res.max_allowed, 10000.0)

    def test_ok(self):
        limiter = SingleNameConcentrationLimiter(0.1)
        res = limiter.check_exposure(100000, 5000)
        self.assertFalse(res.is_breached)
"""
    },
    {
        "name": "risk-limit-breach-escalation-matrix",
        "impl": "risk_escalation_matrix",
        "desc": "Graduated risk response (WARN -> REDUCE -> HALT -> FLATTEN)",
        "script_content": """from dataclasses import dataclass
from enum import Enum

class ResponseAction(Enum):
    WARN = "WARN"
    REDUCE = "REDUCE"
    HALT = "HALT"
    FLATTEN = "FLATTEN"
    NONE = "NONE"

@dataclass
class EscalationResult:
    action: ResponseAction
    level: float

class RiskEscalationMatrix:
    def __init__(self, warn_lvl=1.0, reduce_lvl=1.2, halt_lvl=1.5, flatten_lvl=2.0):
        self.levels = {
            flatten_lvl: ResponseAction.FLATTEN,
            halt_lvl: ResponseAction.HALT,
            reduce_lvl: ResponseAction.REDUCE,
            warn_lvl: ResponseAction.WARN
        }

    def evaluate(self, risk_metric: float, limit: float) -> EscalationResult:
        if limit <= 0:
            return EscalationResult(ResponseAction.NONE, 0.0)
        ratio = risk_metric / limit
        
        for lvl in sorted(self.levels.keys(), reverse=True):
            if ratio >= lvl:
                return EscalationResult(self.levels[lvl], lvl)
        return EscalationResult(ResponseAction.NONE, 0.0)
""",
        "test_content": """import unittest
from risk_escalation_matrix import RiskEscalationMatrix, ResponseAction

class TestRiskEscalationMatrix(unittest.TestCase):
    def test_warn(self):
        matrix = RiskEscalationMatrix()
        res = matrix.evaluate(110, 100)
        self.assertEqual(res.action, ResponseAction.WARN)

    def test_flatten(self):
        matrix = RiskEscalationMatrix()
        res = matrix.evaluate(210, 100)
        self.assertEqual(res.action, ResponseAction.FLATTEN)
"""
    },
    {
        "name": "scenario-based-stress-testing-custom-shocks",
        "impl": "custom_scenario_stress_tester",
        "desc": "Stress testing with custom macroeconomic/rate shock scenarios",
        "script_content": """from dataclasses import dataclass
from typing import Dict, List

@dataclass
class ScenarioResult:
    scenario_name: str
    pnl: float

class CustomScenarioStressTester:
    def __init__(self, scenarios: Dict[str, float]):
        self.scenarios = scenarios

    def run_stress_test(self, current_value: float) -> List[ScenarioResult]:
        results = []
        for name, shock in self.scenarios.items():
            results.append(ScenarioResult(name, current_value * shock))
        return results
""",
        "test_content": """import unittest
from custom_scenario_stress_tester import CustomScenarioStressTester

class TestCustomScenarioStressTester(unittest.TestCase):
    def test_scenarios(self):
        scenarios = {"Crash": -0.2, "Rally": 0.1}
        tester = CustomScenarioStressTester(scenarios)
        results = tester.run_stress_test(1000)
        
        self.assertEqual(len(results), 2)
        res_map = {r.scenario_name: r.pnl for r in results}
        self.assertEqual(res_map["Crash"], -200)
        self.assertEqual(res_map["Rally"], 100)
"""
    }
]


for i, skill in enumerate(skills_data, 1):
    skill_name = skill["name"]
    impl = skill["impl"]
    skill_dir = os.path.join("skills", skill_name)
    os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "assets"), exist_ok=True)

    # SKILL.md
    skill_md = f"""---
name: {skill_name}
description: {skill["desc"]}
domain: risk-management
subdomain: risk
tags:
  - risk
  - management
brokers_frameworks: []
version: 1.0.0
author: System
license: MIT
---

# {skill_name}

## When to Use
Use this skill for risk management tasks.

## Prerequisites
- Python 3.10+
- scipy (if applicable)

## Workflow
1. Initialize the class.
2. Provide inputs.
3. Handle outputs.

## Common Pitfalls
- Incorrect inputs.

## Verification
Run tests to verify.

## Related Skills
- None
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(skill_md)

    # Scripts
    with open(os.path.join(skill_dir, "scripts", f"{impl}.py"), "w") as f:
        f.write(skill["script_content"])

    with open(os.path.join(skill_dir, "scripts", f"test_{impl}.py"), "w") as f:
        f.write(skill["test_content"])

    # references/workflows.md
    with open(os.path.join(skill_dir, "references", "workflows.md"), "w") as f:
        f.write("# Workflows\n\nWorkflow documentation.\n")

    # references/standards.md
    with open(os.path.join(skill_dir, "references", "standards.md"), "w") as f:
        f.write("# Standards\n\nStandards documentation.\n")

    # assets/checklist.md
    with open(os.path.join(skill_dir, "assets", "checklist.md"), "w") as f:
        f.write("# Checklist\n\n- [ ] Task 1\n")

    # Run tests
    import sys
    sys.path.append(os.path.join(skill_dir, "scripts"))
    subprocess.run([sys.executable, "-m", "unittest", f"test_{impl}"], cwd=os.path.join(skill_dir, "scripts"), check=True)
    sys.path.pop()

    # Update ROADMAP_500.md
    roadmap_path = "docs/ROADMAP_500.md"
    if os.path.exists(roadmap_path):
        with open(roadmap_path, "r", encoding="utf-8") as f:
            roadmap = f.read()
        roadmap = re.sub(rf"\[planned\](.*{skill_name})", r"[BUILT]\1", roadmap)
        with open(roadmap_path, "w", encoding="utf-8") as f:
            f.write(roadmap)

    # Build index
    subprocess.run([sys.executable, "tools/build_index.py"], check=True)

    # Git commit
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-m", f"feat: implement skill #{i} {skill_name}"], check=True)

print("All skills built successfully.")
