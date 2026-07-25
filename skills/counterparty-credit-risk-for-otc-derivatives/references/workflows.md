# Deep Workflow Reference — counterparty-credit-risk-for-otc-derivatives

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Calculate PFE95**: $PFE_{95\%} = \max\left(0, \sum MTM + 1.645 \cdot \sigma_V \sqrt{T_{\text{max}}}\right)$.
2. **Compute CVA**: $CVA = (1 - R) \cdot PFE_{95\%} \cdot PD$.
3. **Audit Credit Limit & ISDA Threshold**: Check $PFE_{95\%} > \text{CreditLimit}$.
4. **Generate Collateral Margin Call**: $\text{MarginCall} = \max(0, PFE_{95\%} - \text{CSA\_Threshold})$.

## Production Implementation Reference

- Reference code: `scripts/otc_counterparty_risk.py` (`CounterpartyCreditRiskManager`, `CounterpartyProfile`, `OTCTrade`).
- Automated unit tests: `scripts/test_otc_counterparty_risk.py`.
