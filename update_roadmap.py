import os
import re

cwd = r"C:\Users\Himanshu Jangir\Downloads\algo-trading-skills (2)\algo-trading-skills-v2"
roadmap_path = os.path.join(cwd, "docs", "ROADMAP_500.md")

with open(roadmap_path, 'r', encoding='utf-8') as f:
    roadmap_content = f.read()

skills = [
    "strategy-decommissioning-and-position-unwind-procedure",
    "portfolio-construction-with-transaction-cost-awareness",
    "meta-strategy-signal-arbitration",
    "strategy-specific-vs-shared-risk-budget-allocation",
    "rebalancing-frequency-optimization-cost-vs-drift",
    "strategy-performance-decay-detection-vs-market-wide-decay",
    "capital-efficiency-across-cross-margined-strategies",
    "strategy-committee-governance-for-capital-allocation-decisions",
    "benchmark-portfolio-for-multi-strategy-performance-context",
    "tail-correlation-between-strategies-under-stress"
]

for skill in skills:
    roadmap_content = roadmap_content.replace(f"**[planned]** `{skill}`", f"**[BUILT]** `{skill}`")

with open(roadmap_path, 'w', encoding='utf-8') as f:
    f.write(roadmap_content)

print("Updated ROADMAP_500.md")
