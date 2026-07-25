---
name: counterparty-credit-risk-for-otc-derivatives
description: >-
  Use when trading bilateral OTC derivatives (swaps, OTC options, forwards) to compute Expected Exposure (EE), Potential Future Exposure (PFE), and Credit Valuation Adjustment (CVA) while enforcing ISDA CSA collateral thresholds.
domain: algorithmic-trading
subdomain: risk-management
tags: ["risk-management", "counterparty-risk", "otc-derivatives", "cva", "pfe", "isda-csa", "credit-limit"]
brokers_frameworks: ["Counterparty Credit Risk Manager", "Python NumPy"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when executing non-exchange-cleared Over-The-Counter (OTC) contracts (e.g. crypto OTC swaps, FX forwards, custom equity swaps). Unlike exchange-cleared futures/options backed by a central clearinghouse (CCP), OTC transactions expose trading firms to bilateral counterparty default risk. This skill measures Potential Future Exposure (PFE), calculates Credit Valuation Adjustment (CVA), and enforces ISDA Credit Support Annex (CSA) collateral margin thresholds.

## Prerequisites

- Counterparty Credit Rating / Probability of Default ($PD$).
- Recovery Rate $R$ (e.g. $40\%$, corresponding to $60\%$ Loss Given Default $LGD = 1 - R$).
- Portfolio mark-to-market value $V_t$ and volatility $\sigma_V$.

## Workflow

1. **Calculate Expected Exposure ($EE$) & Potential Future Exposure ($PFE_{95\%}$)**:
   $$PFE_{95\%} = \max\left(0, V_t + 1.645 \times \sigma_V \sqrt{T}\right)$$

2. **Compute Credit Valuation Adjustment (CVA)**:
   $$CVA = (1 - R) \times PFE_{95\%} \times PD$$

3. **Check ISDA CSA Collateral Threshold Breach**:
   If $PFE_{95\%} > \text{UncollateralizedCreditLimit}$, trigger collateral posting margin call:
   $$\text{MarginCallAmount} = PFE_{95\%} - \text{CSA\_Threshold}$$

4. **Audit Counterparty Exposure Cap**: Block new OTC trades if counterparty limit is exceeded.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating OTC Counterparties Like Exchange CCPs**: Assuming bilateral OTC contracts have zero default risk.
- **Ignoring Netting Agreements**: Calculating gross exposures without applying bilateral netting agreements across master agreements.

## Verification

- Submit OTC position with $\$2,000,000$ PFE against counterparty with $\$1,000,000$ credit limit, verify margin call generation and trade blocking.
- Run `python scripts/test_otc_counterparty_risk.py` and confirm 100% pass rate.

## Related Skills

- `counterparty-and-broker-concentration-risk`
- `margin-utilization-circuit-breaker`
---
