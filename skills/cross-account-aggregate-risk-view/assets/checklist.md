# Pre-Flight / Sign-off Checklist — cross-account-aggregate-risk-view

Use this before considering the skill's implementation complete.

- [ ] **Complete Registration:** All sub-accounts and prime broker accounts are registered in the central risk engine (re-register an account to refresh its balances).
- [ ] **Consolidated Valuation:** Total firm NAV and Gross Market Value (GMV) are computed on consolidated net positions — gross across symbols, net within each symbol.
- [ ] **Fail-Closed Pricing:** Every held symbol has a valid market price (> 0, finite); missing/zero/NaN prices produce an `unvalued_symbols` violation and block approval instead of valuing positions at $0.00.
- [ ] **Offsetting Audit:** Internal offsetting long/short positions are detected and alerted as capital friction — not asserted as regulatory wash-trade violations.
- [ ] **Pre-Trade Gate:** Every order is checked against firm-wide aggregate limits before submission; unknown accounts are rejected; risk-reducing orders remain approvable during a breach.
- [ ] **Margin Sanity:** Margin used against zero/unconfigured aggregate capacity is treated as a violation, never reported as 0% utilization.
- [ ] **Pre-Trade Margin Projection:** If the aggregate margin cap is meant to gate new orders, `additional_margin_usd` is supplied on every `evaluate_pre_trade_order` call — the default of 0.0 gates GMV only. The margin figure itself comes from the broker's Reg T / portfolio-margin / SPAN calculation, not from this engine.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/cross-account-aggregate-risk-view/scripts` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
