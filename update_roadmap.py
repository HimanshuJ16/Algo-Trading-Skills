import os

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

# Read and update docs/ROADMAP_500.md
roadmap_path = os.path.join(base_dir, "docs", "ROADMAP_500.md")
with open(roadmap_path, "r", encoding="utf-8") as f:
    content = f.read()

for skill_name, _ in skills:
    content = content.replace(f"- **[planned]** `{skill_name}`", f"- **[BUILT]** `{skill_name}`")

with open(roadmap_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Updated ROADMAP_500.md")
