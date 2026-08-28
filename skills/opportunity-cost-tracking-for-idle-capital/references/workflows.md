# Deep Workflow Reference — opportunity-cost-tracking-for-idle-capital

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

0. **Reconcile the capital snapshot before measuring anything**:
   - Assert $|(\text{Allocated} + \text{Unallocated}) - \text{Total}| \le$ tolerance. An idle
     ratio computed against an unreconciled total is not interpretable, and an
     unvalidated `allocated_capital` field lets unallocated cash exceed total capital
     and report a $200\%$ idle ratio as a sweep signal.
   - Reject a **negative** cash balance. That is a margin debit — a borrowing cost, not
     an opportunity cost. Unchecked it yields a negative ratio and a negative drag,
     which reads as a surplus and reports `IDLE_RATIO_HEALTHY` on the one account that
     is actively paying interest.
   - Reject non-finite rates and a non-positive holding period. A `NaN` rate propagates
     to a `NaN` drag, fails the `> 0` profitability test, and returns
     `MAINTAIN_IDLE_CASH` — a confident recommendation backed by no data.

1. **Compute the net benchmark spread**:
   $$r_{\text{net}} = r_{\text{benchmark}} - r_{\text{cash}}$$
   - Opportunity cost is the yield *foregone*. Idle cash in a brokerage or custody
     account usually earns broker credit interest or a sweep money-market yield;
     charging the full benchmark overstates the drag by exactly that amount.
   - $r_{\text{net}} \le 0$ means the cash is already placed at least as well as the
     benchmark. There is nothing to recover, and the recommendation is
     `MAINTAIN_IDLE_CASH` with reason `NO_YIELD_ADVANTAGE`.
   - Both fields are in **percent**. A value with magnitude below $0.5$ is warned on as a
     likely unconverted fraction ($0.0525$ where $5.25$ was meant understates $100\times$).

2. **Accrue the spread over the holding period**:
   - Choose the day count from how the rate is *quoted*: SOFR, the SOFR Averages, the
     SOFR Index, and T-bills are **ACT/360**; SONIA and coupon Treasury yields are
     ACT/365F. See `references/standards.md` for sources.
   $$\text{PeriodYield} = \frac{r_{\text{net}}}{100} \times \frac{\text{Days}}{\text{Basis}}
     \quad\text{(SIMPLE)}$$
   $$\text{PeriodYield} = \left(1 + \frac{r_{\text{net}}/100}{\text{Basis}}\right)^{\text{Days}} - 1
     \quad\text{(DAILY\_COMPOUNDED)}$$
   - `DAILY_COMPOUNDED` compounds every calendar day and therefore slightly overstates
     the published SOFR Index, which compounds on business days only. For an exact
     realized figure, use the ratio of two published Index values.
   - $\text{GrossDrag}_{\text{USD}} = \text{UnallocatedCash} \times \text{PeriodYield}$, measured on
     the **full** idle balance — the operational buffer costs yield too.

3. **Report period and annualized drag separately**:
   $$\text{Drag}^{\text{period}}_{\text{bps}} = \frac{\text{GrossDrag}}{\text{TotalCapital}} \times 10{,}000$$
   $$\text{Drag}^{\text{ann}}_{\text{bps}} = \text{IdleRatio} \times \frac{r_{\text{net}}}{100} \times 10{,}000$$
   - The first scales with the holding period; the second does not. Labelling a $30$-day
     period figure "annualized" understates the drag roughly $12\times$.

4. **Size the sweepable balance**:
   $$\text{Sweepable} = \max(0,\ \text{UnallocatedCash} - \text{OperationalBuffer})$$
   - The buffer covers margin calls and settlement obligations and must stay liquid; a
     T+1 money-market redemption does not arrive in time for an intraday call. It is
     excluded from the sweep decision but retained in the reported drag.

5. **Test the sweep economics**:
   $$\text{NetYieldGain} = \text{Sweepable} \times \text{PeriodYield} - \text{SweepCost}_{\text{round-trip}}$$
   $$\text{Breakeven} = \frac{\text{SweepCost}_{\text{round-trip}}}{\text{PeriodYield}}$$
   - `sweep_transaction_cost_usd` is the **all-in round trip** — out and back. Charging
     the outbound leg alone makes marginal sweeps look profitable when the return leg
     is what tips them negative.
   - Recommend `SWEEP_TO_YIELD_BENCHMARK` only when $r_{\text{net}} > 0$ **and**
     $\text{Sweepable} \ge \text{MinSweepThreshold}$ **and** $\text{NetYieldGain} > 0$. The
     profitability test is strict: exactly breaking even is not a reason to transact.
   - Otherwise return `MAINTAIN_IDLE_CASH` with a `sweep_blocked_reason` naming the gate
     that failed (`NO_YIELD_ADVANTAGE`, `BELOW_MIN_SWEEP_THRESHOLD`,
     `SWEEP_COST_EXCEEDS_YIELD`, `BELOW_MIN_SWEEP_THRESHOLD_AND_UNECONOMIC`), so the
     decision is auditable rather than a bare negative.

6. **Audit the idle ratio threshold**:
   - Flag `IDLE_CAPITAL_RATIO_EXCEEDED` when $\text{IdleRatio} > \text{TargetIdleRatioMax}$.
     The comparison is strict: sitting exactly at the cap is compliance, not breach.
   - `target_idle_ratio_max` is a **fraction**; `5` meaning "5%" is rejected because it
     would silently disable the alert for every possible portfolio.

7. **Audit Report Generation**:
   - Emit `OpportunityCostReport` carrying the inputs, both drag measures, the sweepable
     balance, the breakeven notional, the day count and accrual method actually used,
     and the blocked reason. A treasury decision that cannot be reconstructed from its
     report is not auditable.

## Production Implementation Reference

- Reference code: `scripts/opportunity_cost_tracking_for_idle_capital.py`
  (`OpportunityCostTrackerEngine`, `PortfolioCapitalState`, `SweepConfig`,
  `OpportunityCostReport`, `DayCount`, `AccrualMethod`, `accrue`).
- Automated unit tests: `scripts/test_opportunity_cost_tracking_for_idle_capital.py`,
  including hand-computed ACT/360 and ACT/365F accruals, the exact $365/360$ convention
  ratio, closed-form breakeven indifference, and rejection of every invalid snapshot.
