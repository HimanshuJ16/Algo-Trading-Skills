---
name: short-selling-borrow-cost-and-availability-modeling
description: >-
  Use when a strategy shorts equities and must price the securities-lending leg correctly — borrow fee accrual on the ACT/360 money-market basis against daily-marked 102% collateral (SIFMA MSLA Sec. 5.1 / 9.1), a fail-closed inventory availability gate, observed broker rates in preference to a utilization heuristic, short-proceeds rebate netting, and open-term lender recall risk under MSLA Sec. 6.1(a).
domain: Market Microstructure & Portfolio Risk
subdomain: Securities Lending & Short Sale Borrow Cost
tags: ["short-selling", "borrow-cost", "hard-to-borrow", "htb-rate", "locate-availability", "securities-lending", "act-360", "recall-risk"]
brokers_frameworks: ["SIFMA 2017 Master Securities Loan Agreement", "Interactive Brokers Stock Borrow Fee Reporting", "S&P Global / DataLend Securities Finance Metrics", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing, backtesting, or executing quantitative short-selling or market-neutral equity strategies. Short selling requires borrowing shares, and the borrow leg has two independent failure modes: the shares may not be there, and the fee may dwarf the alpha. General Collateral (GC) names lend at a few tens of basis points; specials run orders of magnitude higher, and the fee reprices daily on an open loan. This module gates the short on inventory, prices the borrow on the conventions the fee is actually charged under, and flags recall exposure.

## When NOT to Use

- **As a Regulation SHO locate.** Rule 203(b)(1) requires the broker-dealer to have borrowed, arranged to borrow, or have reasonable grounds to believe it can borrow. `check_availability()` compares a requested size against a reported inventory number — it creates nothing and it is not a locate record. Use `us-reg-sho-short-sale-locate-requirements` for the compliance gate.
- **As a guarantee the borrow survives the trade.** US equity loans are open term. MSLA Sec. 6.1(a) lets *either* party terminate on notice, with the termination date no earlier than standard settlement. Availability today says nothing about availability on day 20.
- **To price a borrow when the broker has quoted you a rate.** The utilization ramp is a fallback for research on data where only utilization is observable. If you have a quoted rate, pass it as `observed_borrow_rate` — no public source defines a functional mapping from utilization to fee, so the ramp is a guess and the quote is not.
- **For non-USD/EUR loans without changing `day_count_basis`.** The default 360 is the money-market convention for USD and EUR. GBP-denominated loans accrue ACT/365 fixed; leaving 360 in place overstates a sterling borrow by ~1.4%.
- **For dividend, corporate-action, or tax modelling.** A short pays a manufactured dividend and may face substitute-payment tax treatment. That is a separate P&L line and is out of scope here.

## Prerequisites

- A per-ticker borrow feed supplying `BorrowStatus(ticker, utilization_rate, available_shares)` — utilization as on-loan quantity over lendable inventory in $[0, 1]$, availability as the shares your lender will actually offer. Neither field has a default; a fabricated inventory number is what makes an availability gate pass silently.
- Where available, the desk's or broker's quoted annualized rate as `observed_borrow_rate`.
- For accurate accrual: the per-day settlement marks over the holding period (`calculate_borrow_cost_schedule`), rather than a single entry price.
- The correct `day_count_basis` for the loan currency (360 for USD/EUR, 365 for GBP).

## Workflow

1. **Availability Gate (fail-closed)**:
   - `check_availability(ticker, shares)` returns a reason code, not just a boolean. **Decision point — an unregistered ticker is a rejection (`NO_BORROW_STATUS`), not a pass.** The absence of borrow data is not evidence of a cheap, freely available borrow; treating it as one is how a backtest shorts a name no lender would have offered.
   - **Decision point — inventory reported at 100% utilization is contradictory data**, not a permissive signal. The module returns `FULLY_UTILIZED` and refuses rather than trusting whichever field is looser.

2. **Rate Resolution**:
   - `resolve_rate()` returns `(rate, source)`. An `observed_borrow_rate` always wins and reports `observed`.
   - Otherwise the heuristic applies: flat `gc_rate` at or below the utilization threshold, then a linear ramp from `htb_base_rate` to `max_htb_rate` up to 100% utilization. **Check `rate_source` before trusting a cost number** — `heuristic_htb` means the rate was interpolated from supply pressure, not quoted.
   - The ramp steps discontinuously at the threshold (0.30% to 5.00% on the defaults). That is a modelling artifact, not a market phenomenon; do not build a signal on the jump.

3. **Fee Accrual**:
   - Collateral base is the Margin Percentage times market value (MSLA Sec. 9; 102% is customary US practice, and IBKR rounds the margined per-share price up to the whole dollar).
   - $\text{DailyFee} = \text{CollateralValue} \times \dfrac{\text{Rate}}{\text{DayCountBasis}}$, accrued **from and including** the open date **to but excluding** the cover date (MSLA Sec. 5.1). Calendar days — weekends accrue.
   - **Decision point — pick the right entry point.** `calculate_borrow_cost(trade)` prices the whole period at one rate and one price and is an approximation. `calculate_borrow_cost_schedule(ticker, shares, daily_marks, daily_rates)` accrues on each day's mark, which is what the fee is actually computed on. Feed **prior-day** settlement marks: accruing day $i$ on day $i$'s own close charges the position against a price it did not yet know.

4. **Net Financing**:
   - The fee is the gross leg. Where short sale proceeds are rebated, set `short_proceeds_credit_rate`; `net_financing_cost_usd` can then be negative (positive carry). Default is no credit, which is conservative for cost but must not be reported as "the rebate is unavailable."

5. **Recall / Squeeze Triage**:
   - `assess_recall_risk()` returns `LOW` / `ELEVATED` / `HIGH` from utilization and offered inventory. These are review triggers, not calibrated probabilities.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Defaulting an unknown borrow to General Collateral.** Pricing a ticker you have no borrow data for at 30bp is the single most effective way to make a short backtest look profitable. Missing data must raise, not fall back to the cheapest possible assumption.
- **Fabricating an inventory default.** An `available_shares` default (this module previously assumed 1,000,000) makes the availability gate approve every name nobody checked. Availability has no safe default.
- **Annualizing on 365.** USD and EUR securities loans accrue ACT/360, and IBKR publishes the divisor as 360. Using 365 understates every borrow fee by about 1.4% — small per trade, systematic across a book.
- **Accruing on the entry price.** MSLA Sec. 5.1 computes the fee daily on that day's market value. A short that doubles against you costs roughly twice as much to carry on the way out, and a flat-entry-price model never sees it.
- **Ignoring the 102% collateral markup.** The fee is charged on collateral, not on bare notional; skipping the Margin Percentage understates cost by a further 2%.
- **Treating today's rate as the term rate.** Open loans reprice daily. Pricing a 30-day hold at the day-1 rate is an assumption about borrow supply 29 days out, not a cost estimate.
- **Reading the utilization ramp as a market relationship.** Utilization is on-loan over lendable inventory from custodial pools. It is a supply-pressure signal, not a fee curve, and it says nothing about what your prime broker will quote you.
- **Confusing availability with a locate, or a locate with a term borrow.** Neither survives a lender recall under MSLA Sec. 6.1(a), and a recall that cannot be replaced ends in a buy-in at the worst possible moment.
- **Passing a signed short quantity.** `shares` is an absolute size; a negative would flip the fee into a credit. The module rejects it.

## Verification

- Instantiate `BorrowCostModeler(gc_rate=0.0025, htb_base_rate=0.05, max_htb_rate=0.30)` — defaults of `day_count_basis=360`, `collateral_margin_pct=1.02`, `htb_utilization_threshold=0.80`.
- Availability: `BorrowStatus("AAPL", 0.10, 100_000)` $\Rightarrow$ `can_short("AAPL", 1000) is True`. `BorrowStatus("MEME", 1.00, 0)` $\Rightarrow$ reason `NO_INVENTORY`. `BorrowStatus("CNTR", 1.00, 5_000)` $\Rightarrow$ reason `FULLY_UTILIZED`. An unregistered ticker $\Rightarrow$ reason `NO_BORROW_STATUS` and `can_short is False`.
- Rate: `BorrowStatus("GME", 0.90, 5_000)` $\Rightarrow$ $0.05 + 0.5 \times (0.30 - 0.05) = 0.175$, source `heuristic_htb`. Utilization exactly $0.80$ $\Rightarrow$ `gc_rate`. Utilization $1.00$ $\Rightarrow$ `max_htb_rate`. Adding `observed_borrow_rate=0.87` $\Rightarrow$ $0.87$, source `observed`. An unregistered ticker $\Rightarrow$ `UnknownBorrowStatusError`.
- Cost: `ShortTrade("AAPL", 100, 150.0, 30)` $\Rightarrow$ collateral $\$15{,}300$, cost $\$3.1875$ (the legacy 365-day bare-notional formula returned $\$3.0822$). `ShortTrade("GME", 100, 20.0, 10)` $\Rightarrow$ $\$9.9167$.
- Schedule: 100 shares over marks $[10, 20, 30]$ at $10\%$ $\Rightarrow$ $\$1.70$, versus $\$0.85$ for the flat-entry-price approximation over the same three days.
- Net financing: `short_proceeds_credit_rate=0.05` on 100 shares at $\$100$ for 36 days $\Rightarrow$ credit $\$50.00$ against a $\$3.06$ fee, i.e. `net_financing_cost_usd` $= -\$46.94$.
- Negative checks: utilization outside $[0,1]$, `NaN` utilization or price, negative `available_shares`, non-positive `shares`, negative `days_held`, `max_htb_rate < htb_base_rate`, `htb_utilization_threshold = 1.0`, `collateral_margin_pct < 1.0`, and a `daily_rates` length mismatch must each raise.
- Run `python scripts/test_borrow_cost_modeler.py` and confirm 100% pass rate.

## Related Skills

- `us-reg-sho-short-sale-locate-requirements`
- `eu-short-selling-regulation-disclosure-thresholds`
- `portfolio-construction-with-transaction-cost-awareness`
- `sec-rule-15c3-5-risk-controls-us`
