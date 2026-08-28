# Standards for Risk Metric Recalculation Frequency Tuning

## 1. What is a regulatory requirement, and what is a house default

The single most important distinction in this skill: **no regulator consulted below prescribes a VaR, Greeks or stress-test recalculation interval.** The tier table in section 3 is an engineering default. What regulators *do* constrain is which controls may be put on a cadence at all, and how fast certain outputs must reach a human.

### 1.1 United States — per-order controls are not tunable

| Item | Detail |
|---|---|
| Regulator / jurisdiction | U.S. Securities and Exchange Commission — broker-dealers with market access |
| Rule | 17 CFR 240.15c3-5 (Market Access Rule) |
| Status | In force; mandatory for broker-dealers with market access |

Verified text, § 240.15c3-5(c)(1)(i) — financial risk management controls must be reasonably designed to:

> "Prevent the entry of orders that exceed appropriate pre-set credit or capital thresholds in the aggregate for each customer and the broker or dealer and, where appropriate, more finely-tuned by sector, security, or otherwise by rejecting orders if such orders would exceed the applicable credit or capital thresholds"

And § 240.15c3-5(c)(1)(ii):

> "Prevent the entry of erroneous orders, by rejecting orders that exceed appropriate price or size parameters, on an order-by-order basis or over a short period of time, or that indicate duplicative orders."

**Implementation impact:** the credit/capital threshold check and the erroneous-order check are properties of order entry, not of a recalculation schedule. They must not be assigned a tier. This skill tiers *analytics* (portfolio VaR, stress grids, aggregate Greeks); it does not tier the order-entry gate.

Two further provisions bear on cadence design:

- § 240.15c3-5(c)(2) requires regulatory risk controls reasonably designed to ensure compliance with regulatory requirements, including preventing order entry unless there has been compliance with requirements "that must be satisfied on a pre-order entry basis", and assuring that "appropriate surveillance personnel receive immediate post-trade execution reports". Execution reporting is likewise not a tunable tier.
- § 240.15c3-5(e)(1) requires the firm to "review, no less frequently than annually, the business activity of the broker or dealer in connection with market access to assure the overall effectiveness of such risk management controls and supervisory procedures". Whatever tier intervals you choose, they fall inside that annual review — record them and the rationale.

*Note on wording:* the phrase "automated pre-trade controls", ubiquitous in vendor material, does **not** appear in the rule text. The rule's own language is "prevent the entry of orders ... by rejecting orders". Do not quote the vendor phrasing as regulation.

Source: 17 CFR 240.15c3-5 (e-CFR / Cornell LII; verified August 2026).

### 1.2 European Union / United Kingdom — a hard five-second bound on alerts

| Item | Detail |
|---|---|
| Regulator / jurisdiction | European Commission / ESMA (EU); FCA (assimilated law, UK) |
| Rule | Commission Delegated Regulation (EU) 2017/589 — MiFID II RTS 6 |
| Applicability | Investment firms engaged in algorithmic trading |
| Status | In force (EU); retained as assimilated law in the UK (FCA Handbook technical standards) |

Verified text, Article 16 ("Real-time monitoring"), paragraph 1:

> "An investment firm shall, during the hours it is sending orders to trading venues, monitor in real time all algorithmic trading activity that takes place under its trading code, including that of its clients, for signs of disorderly trading, including trading across markets, asset classes, or products, in cases where the firm or its clients engage in such activities."

Article 16(5) — the only numeric latency bound in this skill's regulatory surface:

> "Real-time alerts shall be generated within five seconds after the relevant event."

Article 17 ("Post-trade controls"), paragraphs 1-2:

> "An investment firm shall continuously operate the post-trade controls that it has in place."
>
> "Post-trade controls ... shall include the continuous assessment and monitoring of market and credit risk of the investment firm in terms of effective exposure."

**Implementation impact:**

- Any metric whose output can raise a disorderly-trading alert must reach the alerting path within 5 s of the triggering event. A 30 s or 300 s tier cannot satisfy that. Either move such a metric to Tier 1/2 or derive the alert from a separate always-on path.
- "Continuously" in Article 17 is not defined numerically. The defensible reading is a documented, monitored cadence — which is why `overdue_metrics` matters: a cadence the scheduler demonstrably fails to meet is not a cadence you can point to in a review.

Source: Commission Delegated Regulation (EU) 2017/589, Articles 16-17 (EUR-Lex CELEX 32017R0589; FCA Handbook assimilated technical standards; verified August 2026).

### 1.3 Not verified — do not assert

- No source was found prescribing a recalculation frequency for VaR, CVaR, Greeks or stress testing for a proprietary algorithmic trading firm. Statements of the form "regulators require intraday VaR every N seconds" should be treated as unsupported unless the reader can cite the instrument themselves.
- Bank capital frameworks (Basel market-risk rules) were **not** verified for this skill and are not cited here. If your entity is a bank subject to an internal-models approach, check the frequency requirements in that framework directly — do not infer them from this table.

## 2. Cadence design rules that follow from the above

| Rule | Consequence |
|---|---|
| A per-order control is never a tiered metric | Credit, capital, price, size, duplicate-order checks stay on the order path |
| An alert-feeding metric is bounded by 5 s (EU/UK) | RTS 6 Art. 16(5); such metrics belong in Tier 1 or Tier 2 |
| Every tier interval is a documented choice | § 15c3-5(e)(1) annual review; RTS 6 Art. 17(1) "continuously operate" |
| A missed cadence must be visible | An unmonitored cadence cannot be evidenced as operating |

## 3. Default tier table (engineering defaults, not regulatory minima)

Chosen for a single-book equity/options engine. Calibrate against measured metric cost and documented risk appetite; record what you used.

| Tier | Metric (default) | Base interval | Accelerated interval | Target risk control |
|---|---|---|---|---|
| 1 — Per-evaluation | `TICK_DRAWDOWN` | 0.0 s | 0.0 s | Real-time drawdown and position caps |
| 2 — Fast | `GREEKS_DELTA` | 2.0 s | 0.5 s | Aggregate option delta / gamma |
| 3 — Medium | `VAR_1DAY` | 30.0 s | 5.0 s | 1-day parametric VaR / CVaR |
| 4 — Slow | `STRESS_TEST` | 300.0 s | 30.0 s | Portfolio stress scenario grid |

Tier 1 intervals must be `0.0`. A tier-1 metric runs on every evaluation, so a non-zero interval would be silently ignored — the config rejects it instead.

## 4. Acceleration parameters (engineering defaults)

| Parameter | Default | Rationale |
|---|---|---|
| `pnl_velocity_threshold_usd_per_sec` | 500.0 | House number for a small single book. Size it against your own P&L distribution, not this table. |
| `min_velocity_sample_sec` | 0.25 | Below this window, tick jitter dominates and `\|ΔPnL\|/Δt` amplifies noise into six-figure velocities. |
| `acceleration_exit_ratio` | 0.5 | Hysteresis band. Exit at half the entry threshold so the mode does not flap sample by sample. |
| `acceleration_min_dwell_sec` | 30.0 | Minimum time in accelerated mode. A crash is not over because one sample was quiet. |
| `staleness_multiple` | 2.0 | A metric more than 2× its interval late indicates a stalled driver, not jitter. |

## 5. Measurement honesty

`calculation_load_reduction_pct` is the ratio of scheduled invocations (optionally weighted by caller-supplied `relative_cost_units`) to a recompute-everything baseline, cumulative since construction. With default unit weights it is an **invocation count, not a CPU-cycle measurement**, and it understates the real saving because a stress grid costs orders of magnitude more than a drawdown update. Report it as what it is. If a CPU figure is needed, profile the engine — the scheduler cannot produce one.
