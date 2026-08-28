# Pre-Flight / Sign-off Checklist — options-flow-unusual-activity-detection

## Input data
- [ ] `volume` is the size of a **single print**, not the series' cumulative session volume.
- [ ] `open_interest` and `adv` are for the **option series**, not the underlying.
- [ ] Unavailable `open_interest` / `adv` are passed as `None`, never as `0`.
- [ ] `bid`/`ask` are the quote **in force at the print**; no zero placeholders for a missing quote.
- [ ] `contract_multiplier` is the series' actual multiplier (100 for standard US listed; verified for adjusted and non-US contracts).
- [ ] It is understood that open interest is the prior session's OCC figure and does not move intraday.

## Metrics
- [ ] $V/OI \ge 1.5$ evaluated, with a zero denominator treated as infinite — not as the raw volume.
- [ ] $V/ADV \ge 2.0$ evaluated.
- [ ] Premium $= V \times P \times M \ge \$100{,}000$ evaluated.
- [ ] All three gates must clear before a print is flagged; `gates_unevaluable` is checked before reading "no flags" as "no unusual activity".

## Direction
- [ ] Aggressor side derived from the quote rule (at/above ask = buy, at/below bid = sell).
- [ ] Missing, non-positive, or crossed quotes yield `UNCLASSIFIED` — never a default buy.
- [ ] Midspread and unclassified prints carry `direction_is_inferred = False` and are excluded from sentiment aggregation.
- [ ] Consumers are aware the quote rule signs ~83% of classifiable option trades correctly (Savickas & Wilson 2003) and that no signal is acted on from a single print.

## Scope and interpretation
- [ ] Multi-leg, spread and delta-hedged flow is understood to print leg by leg and to be indistinguishable from directional risk here.
- [ ] `UNUSUAL_*_BLOCK` labels are understood to assume an opening trade the feed cannot confirm.
- [ ] `UNUSUAL_*_SWEEP` is understood as the classic screen, not a verified multi-venue sweep.
- [ ] Thresholds have been calibrated per underlying / liquidity tier and the rationale recorded — they are library defaults, not industry standards.

## Testing
- [ ] Automated Testing: Run `python scripts/test_options_flow_unusual_activity_detection.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
