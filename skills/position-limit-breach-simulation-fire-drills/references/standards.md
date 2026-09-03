# Standards — position-limit-breach-simulation-fire-drills

## What is actually mandated, and by whom

Jurisdiction matters. Nothing below applies universally; establish which regime binds
the desk before citing any of it in a drill report.

| Requirement | Source | Scope | Mandatory? |
|---|---|---|---|
| Intraday positions in excess of a limit are violations, not just end-of-day positions | CME/CBOT/NYMEX/COMEX **Rule 562** ("Position Limit Violations"), reproduced in CME Group Market Regulation Advisory Notice **RA2601-5**, effective 12 March 2026 ([CFTC rule filing](https://www.cftc.gov/filings/orgrules/rules02252639843.pdf)) | Positions on CME Group designated contract markets | Mandatory |
| One business day to liquidate an overage caused by an **option assignment**; a position exceeding limits on today's delta factors but not yesterday's is not a violation; a clearing member carrying customer excess has a reasonable period, generally not exceeding one business day | CME Rule 562 (same source) | As above | Mandatory carve-outs |
| A bona fide hedge application filed within **five business days** of assuming an over-limit position is not a Rule 559 violation | CME **Rule 559** ("Position Limits and Exemptions"), same advisory | As above | Mandatory |
| No person may hold or control positions in the spot month, or in a single month / all-months-combined, in excess of Commission levels | **17 CFR 150.2** ([eCFR / LII](https://www.law.cornell.edu/cfr/text/17/150.2)) | Referenced contracts based on the enumerated core referenced futures contracts | Mandatory. The rule text does not itself distinguish intraday from end-of-day measurement; the exchange rule above does. |
| Controls "reasonably designed" to prevent entry of orders exceeding pre-set credit or capital thresholds | **SEC Rule 15c3-5(c)(1)(i)** ([17 CFR 240.15c3-5](https://www.law.cornell.edu/cfr/text/17/240.15c3-5)) | US broker-dealers with market access — **not** futures activity | Mandatory, principles-based |
| Review of the effectiveness of those controls **no less frequently than annually**, plus annual CEO certification | SEC Rule 15c3-5(e)(1) and (e)(2) | As above | Mandatory — this is the obligation a fire drill programme services |
| Annual self-assessment and validation report covering algorithmic trading systems, governance and business continuity | **RTS 6 Art. 9** (Commission Delegated Regulation (EU) 2017/589) | EU/UK investment firms engaged in algorithmic trading | Mandatory |
| Stress testing of the Art. 12–18 controls as part of that self-assessment, carried out "in such a way that they do not affect the production environment" | **RTS 6 Art. 10** | As above | Mandatory — the basis for this skill's production guard |
| Ability to cancel immediately any or all unexecuted orders ("kill functionality") | **RTS 6 Art. 12(1)** | As above | Mandatory |
| Pre-trade controls: price collars, maximum order values, maximum order volumes, maximum message limits | **RTS 6 Art. 15(1)** | As above | Mandatory. Note this list does **not** contain position limits. |
| Repeated automated execution throttle; after a pre-determined number of executions the system "shall be automatically disabled until re-enabled by a designated staff member" | **RTS 6 Art. 15(3)** | As above | Mandatory — the basis for asserting `manual_reenable_required` |
| Automatic block or cancellation of orders that "risk compromising the investment firm's own risk thresholds", applied to exposures by client, instrument, trader, desk or firm | **RTS 6 Art. 15(5)** | As above | Mandatory — the pre-trade hook for exposure limits |
| Documented, temporary, exceptional procedure for submitting an order the pre-trade controls blocked, verified by risk management and authorised by a designated individual | **RTS 6 Art. 15(6)** | As above | Mandatory — the only legitimate override path |
| Real-time alerts "generated within five seconds after the relevant event" | **RTS 6 Art. 16(5)** | As above | Mandatory — the only published latency number in this area |
| For derivatives, post-trade controls "regarding the maximum long and short and overall strategy positions" | **RTS 6 Art. 17(4)** | As above | Mandatory — position limits are a *post-trade* control here |
| Where a post-trade control triggers: adjust or shut down the algorithm/system, or withdraw from the market in an orderly manner | **RTS 6 Art. 17(1)** | As above | Mandatory |
| MiFID II position limits confined to agricultural commodity derivatives and critical or significant commodity derivatives | **Directive 2014/65/EU Art. 57** as amended by **Directive (EU) 2021/338**, applicable from 28 February 2022 | EU commodity derivatives | Mandatory, but narrower than pre-2022 |

## Configuration defaults (calibrate before use)

These are library defaults. Only one of them traces to a published number.

| Parameter | Default | Basis |
|---|---|---|
| `max_pre_trade_latency_ms` | `5.0` | **Internal SLA, not a standard.** No regulator publishes a maximum pre-trade risk-check latency: SEC Rule 15c3-5 is a "reasonably designed" test, and RTS 6 sets no order-rejection deadline. Calibrate against venue throughput and record the rationale. |
| `max_alert_latency_ms` | `5000.0` | RTS 6 Art. 16(5) — real-time alerts within five seconds of the relevant event. Applies to firms in scope of RTS 6. |
| `require_negative_control` | `True` | Not a regulatory requirement. A test-design rule: a suite without an `ALLOW` case cannot detect over-blocking. |
| `require_post_trade_scenario` | `True` | Test-design rule grounded in RTS 6 Art. 17(4) and the CME Rule 562 assignment/delta cases — breaches that arrive without an order. |
| `environment` | `STAGING` | RTS 6 Art. 10; `PRODUCTION` is refused outright. |

## Claims deliberately **not** made

- That any regulator mandates a millisecond-scale pre-trade rejection SLA. None found.
- That latency above any particular figure "causes queuing under fast market conditions". Queuing behaviour depends on the venue's gateway, message rate and the firm's own throttles; no general threshold is supportable.
- That MiFID II position limits apply to all instruments. Since 28 February 2022 they do not.
- That 17 CFR 150.2 expressly requires intraday measurement. It does not say so; the CME exchange rule does.
