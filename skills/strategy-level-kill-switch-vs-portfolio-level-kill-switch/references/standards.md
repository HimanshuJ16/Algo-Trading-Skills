# Standards for Strategy-Level vs Portfolio-Level Kill Switch

**Read this first.** No rule surveyed below sets a strategy or portfolio drawdown *number*, a
cascade count, or a cooldown duration for a trading firm. The `10%` / `15%` / 3-strategy /
24-hour defaults in
`scripts/strategy_level_kill_switch_vs_portfolio_level_kill_switch.py` are **operational risk
policy you set for yourself** — never describe them to a regulator, an auditor or a user as
regulatory minimums, and never present the table below as a source for them. What the rules
*do* establish is the two-tier **shape** of the control: an automatic disable scoped to a
single algorithmic strategy, a separate set of exposure controls that can be applied to the
firm as a whole, limits set from the firm's own capital base, order-level attribution back to
the owning algorithm, and a resume that only a designated human can perform.

## Engineering defaults used by this skill

| Tier | Engineering default (this skill's policy, not a mandate) | Action |
|---|---|---|
| Strategy-Level Kill Switch | `10.0` percentage points of strategy drawdown, net of settled capital flows | Latch and halt/liquidate the SINGLE breaching strategy; siblings continue. |
| Portfolio-Level Kill Switch | `15.0` percentage points of fund drawdown, net of settled capital flows | Latch and halt the fund; fan out to every not-already-latched strategy. |
| Cascade Threshold | `>= 3` strategies latched **by their own measured drawdown** | Triggers the master portfolio kill switch independently of fund drawdown. |
| Breach threshold semantics | `>=` — a drawdown exactly equal to the limit breaches | Decided on the unrounded value. |
| Unevaluable input (`NaN`/`Inf`/non-numeric/non-positive peak) | Latched halt, **no** liquidation, **not** counted toward the cascade | Halt new risk and escalate to a human. |
| Re-enable dwell (`cooldown_seconds`) | `86400.0` (24 hours) | A *minimum dwell gating* an audited human re-enable — never an auto-resume. |

Calibrate the two drawdown limits against your own realized drawdown history rather than
adopting the defaults; see `risk-limit-calibration-against-historical-drawdowns`. Set the
cascade threshold relative to the number of strategies actually registered — the engine warns
when it exceeds the roster and therefore can never fire.

## Regulatory shape of the two tiers

| Jurisdiction / Framework | Binds | What it actually requires |
|---|---|---|
| **EU — MiFID II RTS 6, Art. 15(3)** (Reg. (EU) 2017/589) | Investment firms engaged in algorithmic trading | "[R]epeated automated execution throttles which control the number of times an algorithmic trading strategy has been applied"; after a pre-determined number of repeated executions "the trading system shall be automatically disabled until re-enabled by a designated staff member". This is the closest regulatory analogue to the **strategy tier**: an automatic disable scoped to one strategy, with a human-gated resume. Note the trigger is a *repetition count*, not a drawdown — the drawdown trigger in this skill is the firm's own policy. |
| **EU — MiFID II RTS 6, Art. 15(4)** | Same | The firm "shall set market and credit risk limits that are based on its capital base, its clearing arrangements, its trading strategy, its risk tolerance, experience" and shall adjust them for the changing impact of orders on the market. Limits must exist and be justified; **no percentage is specified**. |
| **EU — MiFID II RTS 6, Art. 15(5)** | Same | Orders that "risk compromising the investment firm's own risk thresholds" must be automatically blocked or cancelled, with controls applied "on exposures to individual clients, financial instruments, traders, trading desks **or the investment firm as a whole**". The firm-as-a-whole clause is the regulatory shape of the **portfolio tier**, and the enumeration of narrower scopes alongside it is the shape of the hierarchy. |
| **EU — MiFID II RTS 6, Art. 12(1)–(3)** ("Kill functionality") | Same | Art. 12(1): ability to "cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues". Art. 12(2): those unexecuted orders include ones originating from individual traders, trading desks or clients. Art. 12(3): the firm must "be able to identify which trading algorithm and which trader, trading desk or, where applicable, which client is responsible for each order that has been sent to a trading venue" — the attribution a **strategy-scoped** kill depends on. Scope is **unexecuted orders**: Art. 12 does *not* mandate flattening open positions, so the `HARD_LIQUIDATE` action here is firm policy, not an Art. 12 obligation. |
| **EU — MiFID II RTS 6, Art. 16** ("Real-time monitoring") | Same | Real-time monitoring of all algorithmic trading activity during order-sending hours, by the trader *and* by risk management or an independent risk control function; "[r]eal-time alerts shall be generated within five seconds after the relevant event", with a process to take remedial action as soon as possible, "including, where necessary, an orderly withdrawal from the market". Note **orderly** — Art. 16 points away from dumping a book at market. |
| **EU — MiFID II RTS 6, Art. 17(2)–(3)** ("Post-trade controls") | Same | Continuous assessment and monitoring of market and credit risk "in terms of effective exposure"; reconciliation of the firm's own electronic trading logs against outstanding orders and risk exposures as reported by venues, brokers, DEA providers, clearing members and CCPs, with the capability to calculate outstanding exposure in real time. This is why both equity inputs to this engine should come from the broker/custodian, not internal bookkeeping. |
| **US — SEC Rule 15c3-5** (17 CFR 240.15c3-5, Market Access Rule) | A **broker or dealer with market access**, or one providing another person access via its MPID — *not* the end trading firm | (b) requires a documented "system of risk management controls and supervisory procedures reasonably designed to manage the financial, regulatory, and other risks of this business activity". (c)(1)(i) requires controls preventing orders that exceed "appropriate pre-set credit or capital thresholds in the aggregate for each customer and the broker or dealer" — the aggregate-threshold concept is the closest US analogue to a portfolio tier, but the obligation sits with the broker-dealer. (d) requires the controls to be "under the direct and exclusive control of the broker or dealer". (e)(1) requires an effectiveness review "no less frequently than annually" — not an intraday drawdown limit. |
| **US — Nasdaq Rule 6130 Kill Switch** (and BX Rule 4764, PSX Rule 3316) | Nasdaq participants, optionally | A venue-side control letting a participant set Risk Exposure levels, after which order-entry ports are disabled and open orders administratively cancelled. Reactivation is **human-gated, not time-gated**: the participant must explain why the switch triggered and why re-authorisation is safe, and Nasdaq operations staff then reactivate the MPID's port. Nothing here establishes a cooldown *duration*. |

## Applicability caveats

- **UK:** RTS 6 was assimilated into UK law post-Brexit and is reproduced in the FCA Handbook
  Technical Standards with unchanged article numbering; confirm the current UK text
  separately rather than assuming indefinite EU/UK parity.
- **India (SEBI):** the retail algo-trading framework places the kill-switch obligation on
  *exchanges*, not on the broker or the trader, and imposes no drawdown number. It is
  surveyed in `kill-switch-and-drawdown-circuit-breakers/references/standards.md`; that skill
  is the reference for SEBI applicability and timelines.
- **Fund and adviser regimes** (UCITS, AIFMD, US registered advisers) impose risk-management
  process and disclosure obligations, but no source consulted for this skill converts them
  into a mandated per-strategy or portfolio drawdown percentage.
- **Not verified here:** nothing in this file establishes a required cooldown duration, a
  required cascade count, a required evaluation frequency, that a kill switch must use market
  orders, or that a strategy-level breach must escalate to a portfolio-level halt at any
  particular multiple. Those are engineering choices. RTS 6 Art. 16 points toward an
  *orderly* withdrawal rather than an immediate market flatten.
- **Scope boundary:** Art. 15(3)'s automatic disable is triggered by a *repeated execution
  count*, not by drawdown. If you need the RTS 6 throttle itself rather than a drawdown
  ladder, that control belongs in the order gateway — see
  `execution-algorithm-kill-switch-integration`.

## Category

`risk-management` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Sources

| Claim | Source |
|---|---|
| RTS 6 Art. 15(3) repeated automated execution throttles controlling "the number of times an algorithmic trading strategy has been applied", automatically disabled "until re-enabled by a designated staff member"; Art. 15(4) limits based on the firm's own capital base and risk tolerance; Art. 15(5) automatic block/cancel and controls on exposures "to individual clients, financial instruments, traders, trading desks or the investment firm as a whole" | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 15 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng |
| RTS 6 Art. 12(1) kill functionality over *unexecuted* orders; Art. 12(2) coverage of orders originating from individual traders, trading desks or clients; Art. 12(3) identification of the responsible trading algorithm, trader, desk or client per order | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 12 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng |
| RTS 6 Art. 16 real-time monitoring, five-second alert generation, remedial action including an orderly withdrawal from the market | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 16 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng |
| RTS 6 Art. 17(2) effective-exposure monitoring; Art. 17(3) reconciliation against venue/broker/CCP data and real-time exposure calculation | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 17 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng |
| Rule 15c3-5 applies to a "broker or dealer with market access"; text of (b), (c)(1)(i), (d), (e)(1) annual review | 17 CFR 240.15c3-5 — https://www.law.cornell.edu/cfr/text/17/240.15c3-5 |
| Nasdaq Kill Switch governed by Rule 6130 (BX 4764, PSX 3316); ports disabled and open orders administratively cancelled on breach of participant-set Risk Exposure levels; reactivation requires the participant to request it and Nasdaq operations to re-authorise the MPID | Nasdaq Equity Kill Switch — https://www.nasdaqtrader.com/content/EquityKillSwitch.pdf |
