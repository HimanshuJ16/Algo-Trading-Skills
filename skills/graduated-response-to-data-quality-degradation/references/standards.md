# Standards — graduated-response-to-data-quality-degradation

## What is standardised, and what is not

**No regulator, exchange or standards body publishes a market-data quality score, a
de-risking tier boundary, or a penalty weight.** Searching the primary sources below
returns rules about *monitoring, kill functionality and business continuity* — never a
numeric data-quality threshold. Every number in the next table is a configurable
engineering default. Do not present the score to a regulator or a vendor as a
compliance metric, and do not treat a tier change as a rule breach.

| Parameter | Default | Status |
|---|---|---|
| `tier_0_min_score` | 90.0 | Engineering default — the floor for unrestricted trading. |
| `tier_1_min_score` | 70.0 | Engineering default. Must be strictly below `tier_0_min_score`. |
| `tier_2_min_score` | 40.0 | Engineering default. Must be strictly below `tier_1_min_score`. |
| `stale_grace_seconds` | 1.0 s | Engineering default — set from the feed's *measured* normal inter-tick gap, not from a vendor SLA. |
| `stale_penalty_per_second` | 10.0 pts | Engineering default. At this weight a 10 s stall alone reaches Tier 3. |
| `missing_sequence_penalty_each` | 2.0 pts | Engineering default. 5 gaps alone leave the score exactly at the Tier 0 bound. |
| `price_spike_penalty` | 25.0 pts | Engineering default. Alone, a spike lands in Tier 1. |
| `crossed_book_penalty` | 50.0 pts | Engineering default. Alone, a crossed book lands in Tier 2 — it blocks entries but does not by itself force a flatten. |
| `spread_grace_multiple` | 2.0x | Engineering default — relative to *your* measured normal spread for that instrument and session. |
| `spread_penalty_per_multiple` | 15.0 pts | Engineering default. |
| `tier_1_size_factor` | 0.50 | Engineering default. |
| `recovery_hold_seconds` | 0.0 (off) | Policy. Left at 0 the engine is memoryless and a single bad tick can trigger a flatten released on the next tick. |

## Engineering standards for a data-quality de-risking gate

| Property | Standard | How this skill meets it |
|---|---|---|
| Fail closed | An input that cannot be evaluated must resolve toward *less* trading, never more. | A `NaN`, infinite, negative or wrongly-typed metric scores 0.0, forces Tier 3, and sets `metrics_valid=False`. |
| Conservative boundaries | Rounding must never promote a score across a threshold in the permissive direction. | Classification uses the exact score; the reported score is floored to 2dp. |
| Unambiguous mandate | "Block entries" and "flatten now" must be distinguishable by the consuming order path. | `allow_new_entries`, `allow_risk_reducing_exits`, `cancel_resting_orders`, `flatten_positions`. `position_sizing_factor` is a new-entry multiplier only. |
| Anti-flap | A graduated control that oscillates is worse than a coarse one that does not. | Escalation is immediate; de-escalation requires `recovery_hold_seconds` of sustained improvement, and a relapse restarts the timer. |
| Monotonic timing | Interval measurement must not depend on wall-clock time, which can step either way. | The recovery hold uses an injected `time.monotonic` clock. |
| Auditability | Why a tier was assigned must be recoverable after the fact. | `penalty_breakdown` and `triggered_conditions` on every report. |
| Configuration validation | Invalid configuration must fail at construction, not silently at run time. | Non-descending tier bounds, negative penalties, out-of-range size factors and non-finite values all raise `ValueError` in `__init__`. |

## Regulatory context

This is engineering guidance, not legal advice. The regimes below apply to *different
populations of firms*, and neither universalises.

### EU / UK — MiFID II RTS 6

Jurisdiction: EU, and the UK as assimilated law. Applies to investment firms engaged in
algorithmic trading. Source: [Commission Delegated Regulation (EU) 2017/589 of 19 July
2016](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589).

| Requirement | Source | Bearing on this skill |
|---|---|---|
| Business continuity arrangements must cover "a range of possible adverse scenarios relating to the operation of the algorithmic trading systems, including the unavailability of systems, staff, work space, external suppliers or data centres or **loss or alteration of critical data** and documents". | RTS 6, Article 14(2)(b) | This is the closest a rule comes to naming the trigger this engine detects. Degraded or altered market data is an in-scope adverse scenario; the tier ladder is one documented arrangement for it. The rule mandates *having* arrangements, not any particular threshold. |
| Arrangements must include "arrangements for shutting down the relevant trading algorithm or trading system where appropriate" and "alternative arrangements for the investment firm to manage outstanding orders and positions". | RTS 6, Article 14(2)(f), 14(2)(g) | Tier 3 (`flatten_positions`) and Tier 2 (`cancel_resting_orders`, exits still permitted) are the software half of these arrangements. The human runbook is the other half. |
| "An investment firm shall ensure that its trading algorithm or trading system can be shut down ... **without creating disorderly trading conditions**." | RTS 6, Article 14(3) | **Directly constrains how Tier 3 is executed.** Flattening at market on prices the engine has just declared unusable is a plausible way to create disorderly conditions. The mandate is to reduce exposure; the execution policy is a separate decision. |
| "An investment firm shall be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues to which the investment firm is connected ('kill functionality')." | RTS 6, Article 12(1) | `cancel_resting_orders` is the *signal*; satisfying Article 12 requires an actual venue-wide cancel path. This engine does not cancel anything. |
| Real-time monitoring of algorithmic trading activity, with "[r]eal-time alerts ... generated within five seconds after the relevant event". | RTS 6, Article 16, and Article 16(5) | Bounds the budget for the whole detect-score-dispatch path if you reuse the RTS 6 alerting channel for feed-health alerts. Note Article 16 governs monitoring of *trading activity* for disorderly trading; it imposes no market-data quality standard. |

### US — market structure

Locked and crossed quotations are governed by **17 CFR 242.610(e)** (Regulation NMS;
this paragraph was designated 610(d) before the amendment at 89 FR 81773). It requires
each national securities exchange and national securities association to maintain rules
that require members "reasonably to avoid ... displaying quotations that lock or cross
any protected quotation in an NMS stock", that are "reasonably designed to assure the
reconciliation of locked or crossed quotations", and that prohibit a pattern or practice
of doing so — "other than displaying quotations that lock or cross any protected or
other quotation **as permitted by an exception contained in its rules**". Source:
[17 CFR § 242.610](https://www.law.cornell.edu/cfr/text/17/242.610).

Two consequences for the `crossed_book_detected` metric:

1. A crossed NBBO in continuous US equities trading is abnormal, which is what makes it
   a credible data-integrity signal.
2. The rule carries explicit exceptions, and locked/crossed conditions occur legitimately
   around auctions, halts and reopens. Scope the check to continuous trading or it will
   de-risk on every open.

The Regulation NMS text does **not** define "locking quotation" or "crossing quotation"
in the definitions at 17 CFR 242.600(b); those descriptions come from the adopting
release rather than the rule text. This skill uses the operational definition **bid
strictly greater than ask on the same instrument at the same instant**, and does not
score a *locked* book (bid equal to ask) at all.

**SEC Rule 15c3-5** (Market Access) requires broker-dealers with market access to
maintain risk controls reasonably designed to prevent erroneous orders. It applies to
broker-dealers, **not** to a self-directed trader running an algorithm through a retail
broker, and it prescribes no data-quality metric. It is noted here only because a
data-quality gate is a common component of an erroneous-order control, not because it
mandates one.

## Category

Real-Time Architecture & Risk / Data Quality Monitoring & De-Risking. See
`data-quality-monitoring-dashboard` for the detection layer this engine consumes, and
`capital-preservation-mode-for-degraded-conditions` for the P&L-driven gate it
deliberately does not duplicate.
