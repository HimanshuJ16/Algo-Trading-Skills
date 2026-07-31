---
name: mas-singapore-algo-trading-guidelines
description: >-
  Pre-trade risk control and regulatory compliance engine enforcing MAS Singapore Securities and Futures Act (SFA) guidelines, SGX price collars, algorithm registration IDs, and emergency kill switches.
domain: Regulatory Compliance Global
subdomain: MAS Singapore SFA & SGX Pre-Trade Risk Controls
tags: ["mas", "singapore", "sfa", "sgx", "algo-registration", "price-collar", "pre-trade-risk", "kill-switch"]
brokers_frameworks: ["MAS Guidelines SFA04-G08", "SGX Securities Trading Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying automated algorithmic trading systems on the Singapore Exchange (SGX) under Monetary Authority of Singapore (MAS) oversight. Under MAS Securities and Futures Act (SFA) Guidelines, all trading algorithms must carry a registered MAS Algorithm Identifier (`mas_reg_number`), pass mandatory pre-deployment sandbox testing, maintain an emergency automated kill switch, and enforce strict pre-trade risk controls (SGX price collars $\le \pm 10\%$, SGD order value limits, and order rate throttles).

## Prerequisites

- MAS algorithm registration config (`algo_id`, `mas_reg_number`, `is_sandbox_tested`, `is_kill_switch_ready`, `max_order_notional_sgd`, `max_order_rate_per_sec`).
- SGX order payload (`algo_id`, `symbol`, `price_sgd`, `quantity`, `sgx_ref_price_sgd`, `current_order_rate_sec`).

## Workflow

1. **MAS Governance & Registration Audit**:
   - Audit `mas_reg_number`, `is_sandbox_tested == True`, and `is_kill_switch_ready == True`.
   - If unregistered or untested $\implies$ Trigger `MAS_REJECT_GOVERNANCE_BREACH`.
2. **SGX Price Collar Audit**:
   - Compute price deviation: $\Delta P_{\%} = \frac{|P_{\text{order}} - P_{\text{ref}}|}{P_{\text{ref}}} \times 100.0$.
   - If $\Delta P_{\%} > 10.0\% \implies$ Trigger `MAS_REJECT_PRICE_COLLAR_BREACH`.
3. **Pre-Trade Value & Rate Limit Audit**:
   - Compute order value in SGD: $V_{\text{sgd}} = P_{\text{sgd}} \times Q$.
   - If $V_{\text{sgd}} > V_{\text{max\_sgd}} \implies$ Trigger `MAS_REJECT_NOTIONAL_EXCEEDED`.
   - If `current_order_rate_sec` $> R_{\text{max\_orders\_sec}} \implies$ Trigger `MAS_REJECT_RATE_LIMIT_EXCEEDED`.
4. **Audit Report Generation**: Output structured `MasComplianceReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Deploying Unregistered Algorithms on SGX**: Submitting orders without valid MAS Algorithm Registration IDs, violating SFA market conduct rules.
- **Bypassing SGX Price Collar Checks**: Allowing limit orders $> \pm 10\%$ away from SGX reference price during market open volatility.
- **Operating Without an Emergency Kill Switch**: Lacking automated kill switch capabilities to instantly cancel open SGX orders upon algorithm malfunction.

## Verification

- Instantiate `MasSingaporeAlgoComplianceEngine`. Audit SGX order (`price_sgd=10.0`, `quantity=1000`, `sgx_ref_price_sgd=10.0`, $V_{\text{sgd}}=\text{SGD }10,000$) with valid MAS registration and kill switch $\implies$ verify `MAS_ORDER_APPROVED`. Audit Price Collar Breach ($P=12.0\text{ SGD}$ vs $P_{\text{ref}}=10.0\text{ SGD}$, $+20\%$ deviation) $\implies$ verify `MAS_REJECT_PRICE_COLLAR_BREACH`.
- Run `python scripts/test_mas_singapore_algo_trading_guidelines.py`.

## Related Skills

- `japan-fsa-high-speed-trading-registration`
- `kill-switch-and-drawdown-circuit-breakers`
---
