# Standards for Portfolio-Level Stop-Loss Independent of Strategy Stops

**Read this first.** No rule surveyed below sets a maximum-drawdown or daily-loss *number*
for a trading firm. The `5%` / `10%` defaults in
`scripts/portfolio_level_stop_loss_independent_of_strategy_stops.py` are **operational risk
policy you set for yourself** — do not describe them to a regulator, an auditor or a user as
regulatory minimums. What the rules below do establish is the *shape* of the control: limits
set from your own capital base and risk tolerance, exposure controls applied to the firm as a
whole, an automatic disable that only a designated person can re-enable, real-time monitoring
with prompt remedial action, and reconciliation of your own view of exposure against the
broker's.

## Engineering defaults used by this skill

| Metric | Engineering default (this skill's policy, not a mandate) |
|---|---|
| Max Daily Drawdown | `0.05` (5%) of start-of-day equity, measured net of settled capital flows. |
| Max Peak Drawdown | `0.10` (10%) of the equity high-water mark, measured net of settled capital flows. |
| Breach threshold semantics | `>=` — a drawdown exactly equal to the limit breaches. |
| Post-Breach Action | One emergency global flatten on the breach transition, plus a latched trading lockout cleared only by an audited `human_re_enable()`. |
| Unevaluable input (`NaN`/`Inf`/non-positive equity/stale marks) | Latched lockout, **no** auto-flatten — halt new risk and escalate. |

Calibrate the two limits against your own realized drawdown history rather than adopting the
defaults; see `risk-limit-calibration-against-historical-drawdowns`.

## Regulatory shape of the control

| Jurisdiction / Framework | Binds | What it actually requires |
|---|---|---|
| **EU — MiFID II RTS 6, Art. 15(4)–(5)** (Reg. (EU) 2017/589, "Pre-trade controls on order entry") | Investment firms engaged in algorithmic trading | Art. 15(4): the firm "shall set market and credit risk limits that are based on its capital base, its clearing arrangements, its trading strategy, its risk tolerance, experience" and shall adjust them "to account for the changing impact of the orders on the relevant market". Art. 15(5): orders that "risk compromising the investment firm's own risk thresholds" must be automatically blocked or cancelled, with controls applied "on exposures to individual clients, financial instruments, traders, trading desks **or the investment firm as a whole**" — the firm-wide clause is the regulatory shape of a portfolio-level stop. No percentage is specified. |
| **EU — MiFID II RTS 6, Art. 15(3)** | Same | Repeated automated execution throttles: after a pre-determined number of repeated executions "the trading system shall be automatically disabled until re-enabled by a designated staff member". This is the shape of the latched lockout plus `human_re_enable()` — automatic disable, human-gated resume. |
| **EU — MiFID II RTS 6, Art. 16** ("Real-time monitoring") | Same | Real-time monitoring of all algorithmic trading activity during order-sending hours, by the trader *and* the risk management or an independent risk control function. "Real-time alerts shall be generated within five seconds after the relevant event", with "a process in place to take remedial action as soon as possible after an alert has been generated, including, where necessary, an orderly withdrawal from the market." Note **orderly** — Art. 16 does not endorse dumping a book at market. |
| **EU — MiFID II RTS 6, Art. 17** ("Post-trade controls") | Same | Art. 17(2): continuous assessment and monitoring of market and credit risk "in terms of effective exposure". Art. 17(3): reconcile the firm's own electronic trading logs against outstanding orders and risk exposures as reported by venues, brokers, DEA providers, clearing members and CCPs — in real time where those parties report in real time — and "have the capability to calculate in real time its outstanding exposure". Art. 17(4): for derivatives, controls over "the maximum long and short and overall strategy positions". |
| **EU — MiFID II RTS 6, Art. 12** ("Kill functionality") | Same | Ability to "cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues". Scope is **unexecuted orders** — Art. 12 does *not* mandate flattening open positions. The global flatten in this skill is its own risk policy, not an Art. 12 obligation. |
| **US — SEC Rule 15c3-5** (17 CFR 240.15c3-5, Market Access Rule) | A **broker or dealer with market access**, or one providing another person access via its MPID — *not* the end trading firm | (c)(1)(i) requires controls "reasonably designed to prevent the entry of orders that exceed appropriate pre-set credit or capital thresholds in the aggregate for each customer and the broker or dealer"; (c)(1)(ii) covers erroneous orders "on an order-by-order basis or over a short period of time". The aggregate-threshold concept is the closest US analogue to a portfolio-level limit, but the obligation sits with the broker-dealer, and (e)(1) requires only an annual review of effectiveness — not an intraday drawdown limit. |

## Applicability caveats

- **UK:** RTS 6 was assimilated into UK law post-Brexit and is reproduced in the FCA Handbook
  Technical Standards with unchanged article numbering; confirm the current UK text
  separately rather than assuming indefinite EU/UK parity.
- **India (SEBI):** the retail algo-trading framework places the kill-switch obligation on
  *exchanges*, not on the broker or the trader, and imposes no drawdown number. It is
  surveyed in `kill-switch-and-drawdown-circuit-breakers/references/standards.md`; that
  skill is the reference for SEBI applicability and timelines.
- **Fund and adviser regimes** (UCITS, AIFMD, US registered advisers) impose risk-management
  process and disclosure obligations, but no source consulted for this skill converts them
  into a mandated portfolio drawdown percentage. Treat any such number as your own policy.
- **Not verified here:** nothing in this file establishes that a portfolio stop *must* use
  market orders, a particular polling frequency, or a particular re-enable workflow. Those
  are engineering choices — RTS 6 Art. 16(5) points the other way, toward an *orderly*
  withdrawal.

## Category

`risk-management` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Sources

| Claim | Source |
|---|---|
| RTS 6 Art. 12 "Kill functionality" wording and its scope over *unexecuted orders* | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 12 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng |
| RTS 6 Art. 15(3) automatic disable "until re-enabled by a designated staff member"; Art. 15(4) risk limits set from the firm's own capital base and risk tolerance; Art. 15(5) firm-as-a-whole exposure controls | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 15 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng |
| RTS 6 Art. 16 real-time monitoring by an independent risk function, five-second alert latency, remedial action including an orderly withdrawal from the market | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 16 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng |
| RTS 6 Art. 17(2) effective-exposure monitoring; Art. 17(3) reconciliation against venue/broker/CCP data and real-time exposure calculation; Art. 17(4) maximum long/short and overall strategy positions for derivatives | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 17 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng |
| Rule 15c3-5 applies to a "broker or dealer with market access"; text of (b), (c)(1)(i), (c)(1)(ii); (e)(1) annual review | 17 CFR 240.15c3-5 — https://www.law.cornell.edu/cfr/text/17/240.15c3-5 |
