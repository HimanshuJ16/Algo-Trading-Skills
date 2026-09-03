---
name: real-time-greeks-recalculation-on-market-moves
description: >-
  Use when an options book must show current Greeks on a live tape and full revaluation
  of every contract on every tick does not fit the CPU budget; decides per position
  between a delta-gamma step and a full reprice.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: risk-management, options-greeks, real-time-risk, taylor-series, delta-gamma-approximation, black-scholes-merton, event-driven
  brokers_frameworks: "Black-Scholes-Merton Closed Form; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when an options book must show current Greeks on a live tape and revaluing every contract on every tick does not fit the CPU budget. The engine decides, per position and per tick, between two things: advancing the cached Greeks with a second-order expansion in spot,

$$\Delta(S_0 + h) \approx \Delta_0 + \Gamma_0 h, \qquad V(S_0 + h) \approx V_0 + \Delta_0 h + \tfrac{1}{2}\Gamma_0 h^2$$

or repricing the contract outright with Black-Scholes-Merton. The expansion is cheap and, over a small move, close enough. Over a large one it is not — which is why the Basel market-risk standard does not accept a delta measure alone for options and computes its curvature charge from *repriced* instruments under an up and a down shock (BCBS, MAR21.5). Small move, expand; large move, reprice.

The part that decides whether this works is not the expansion. It is the **anchor**: what "the move" is measured against.

## When NOT to Use

- **As a pricing or implied-vol engine.** The reval consumes the implied vol handed in for that strike and inherits every property of the surface that produced it. Build the surface first — `options-implied-volatility-surface-construction`.
- **For American-style contracts where early exercise is live.** The closed form is European with a continuous dividend yield. Deep-ITM puts and calls into a dividend need a different model — `american-vs-european-style-option-exercise-handling`.
- **To net a multi-underlying book.** A tick is one price for one underlying, and this engine reports only that underlying's nets. Cross-underlying aggregation, dollar-normalisation and limit auditing belong to `options-greeks-real-time-portfolio-aggregation`.
- **As a hedger or a kill switch.** The report is an observation. Sizing the offsetting trade is `greeks-based-portfolio-hedging-automation`; halting is `kill-switch-and-drawdown-circuit-breakers`, which must be independent of this path.
- **Through expiry on at-the-money strikes.** Delta is discontinuous through the pin and gamma is unbounded; no expansion of either is stable at any refresh rate. The engine degrades to always-reprice inside the pin horizon and you should still read `options-pin-risk-management-at-expiry`.
- **As a latency optimisation you have not measured.** If the book is small enough to reprice wholesale inside the tick budget, do that — it is strictly more accurate and has no anchor to get wrong. Approximate only once the profile says you must.

## Prerequisites

- Contract terms per leg: `symbol`, `underlying_symbol`, `option_type` (`CALL`/`PUT`), `strike`, signed `position_qty`, and `multiplier`.
- **`multiplier` — deliverable units per contract, read from the contract master. Required, no default.** 100 for a standard *unadjusted* US equity option; different for OCC-adjusted contracts, index products and crypto.
- Current market inputs per leg, refreshed each tick: `implied_vol` (e.g. `0.20`) and `time_to_expiry_years` ($>0$). Optionally `risk_free_rate` and `dividend_yield` (both default to `0.0`, i.e. an explicit zero assumption).
- One `spot` per tick, per underlying, passed to the call — not held per position.
- A caller-supplied monotonic `tick_timestamp_s`. Without it the staleness trigger cannot fire.
- Trigger thresholds (`RecalculationTriggerConfig`). The defaults are illustrative starting points, **not** a standard — see `references/standards.md`.

## Workflow

1. **Validate the tick and every leg before anything is published.**
   - Reject a non-positive or non-finite spot, a blank symbol, a non-positive `multiplier`, `strike`, `implied_vol` or `time_to_expiry_years`, an unknown `option_type`, and a duplicate symbol within one tick.
   - **Decision point — a NaN spot does not read as an error, it reads as a *small move*.** `abs(nan) > threshold` is `False`, so an unvalidated NaN never trips the reval trigger and pins the entire book on the cached Greeks indefinitely. Raise on the tick.
   - **Decision point — reject the whole tick, not the bad leg.** A partial Greeks snapshot is a risk number with an unknown fraction of the book missing from it. That is worse than no number, because it looks like one.
   - **Decision point — `option_type` must be validated, not branched on.** `if t == "CALL": ... else: # PUT` turns `"C"` into a put and flips the sign of delta. A mis-signed delta does not look wrong; it looks like the other side of the book.

2. **Restrict the tick to its own underlying.**
   - A tick carries one price for one name. Legs on any other underlying are excluded and counted, never repriced at this spot.
   - **Decision point — this is the structural fix for stale-spot Greeks.** If each position carries its own `spot` field, a book ends up netting Greeks computed at three different prices for the same name and nothing in the output says so. One spot per tick makes that unrepresentable.

3. **Test the current tick against the anchor — the last *full revaluation*, never the last tick.**
   $$\text{drift} = \frac{|S_t - S_{\text{anchor}}|}{S_{\text{anchor}}}$$
   - **Decision point — this is the defect the whole skill exists to prevent.** Reset the baseline to the previous tick and a monotone run of sub-threshold ticks never trips the threshold: sixty consecutive $+0.1\%$ ticks against a $0.5\%$ threshold move spot $+6.2\%$ with **zero** revaluations, because each individual step is "small". The book is carried that entire distance on a frozen gamma. Anchor on the last reval and the same sequence reprices repeatedly.
   - Reprice when *any* of these fires, and record which one: no anchor yet; inside the near-expiry horizon; spot drift past the threshold; implied vol moved past its band; the anchor is older than the staleness cap; the stepped delta left its admissible band.
   - **Decision point — a vol move invalidates the cache with no spot move at all.** $\Gamma$ and $\nu$ are functions of $\sigma$. A book on a flat tape through a vol repricing has stale gamma and stale vega and a spot-only trigger will never notice.
   - **Decision point — a quiet book still drifts.** Theta and charm move the Greeks with no tick at all. Without a staleness cap, an illiquid name's Greeks are as old as its last print.
   - **Decision point — order the tests by validity, not by cost.** Expiry is checked before spot: inside the pin horizon the expansion is the wrong *shape*, not merely imprecise, so how small the move was is irrelevant.

4. **Step or reprice.**
   - Step: $\Delta \leftarrow \Delta_0 + \Gamma_0 h$, with $\Gamma$, $\nu$, $\Theta$ carried forward frozen. Their own drift (speed, vanna, charm) is second order over a sub-threshold move — which is exactly what step 3 bounds.
   - **Decision point — bound the stepped delta.** A call delta lives in $[0, 1]$ and a put delta in $[-1, 0]$. A linear step through a high-gamma region can produce $1.4$; publishing it is worse than the cost of the reprice it should have triggered.
   - Reprice: full BSM with continuous dividend yield. Re-anchor spot, vol, expiry, timestamp, price and all four Greeks in one atomic update — a half-updated anchor is a permanent, silent bias.

5. **Report the nets and the evidence to calibrate on.**
   - Nets for the ticked underlying, summed with `math.fsum` so ordering cannot change the answer, and scaled by the deliverable exactly as `options-greeks-real-time-portfolio-aggregation` scales them.
   - Every reval reports `spot_taylor_value_error_per_unit`: the true value change at the new spot (at the **anchor's** vol and expiry, so the comparison isolates spot) minus the delta-gamma estimate it replaced. **This is how the threshold gets calibrated** — negligible error means CPU is being burned on a threshold that is too tight; material error means the published delta was wrong between revals.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Anchoring the move test on the previous tick.** The single defect that makes this architecture dangerous rather than merely approximate. A trending tape of individually-small ticks never trips the threshold, and the book runs arbitrarily far from the last real price on a frozen gamma. Anchor on the last full revaluation.
- **Treating a small move as a small *error*.** The expansion freezes gamma, so the delta error grows with $\tfrac{1}{2}\,(\partial\Gamma/\partial S)\,h^2$ — and near expiry or near the strike $\partial\Gamma/\partial S$ is large. The same 0.5% move that is negligible on a one-year option is not negligible on a one-day one.
- **Triggering on spot only.** Gamma and vega are functions of implied vol. A vol repricing on a flat tape leaves every cached Greek stale and a spot-only trigger silent.
- **No staleness cap.** Theta and charm move the Greeks with no tick at all, so an illiquid name's "real-time" Greeks are as old as its last print.
- **Clamping expiry with `max(1e-4, T)`.** An expired contract has no Black-Scholes delta. Substituting a floor reports a confident number for a position that has none, and the book keeps hedging against it.
- **Silently defaulting `option_type` to PUT.** An `if CALL / else` branch sign-flips delta on any typo, and a sign-flipped delta reads as a legitimate position on the other side.
- **Hard-coding the multiplier at 100.** The OCC holds the *premium* multiplier at 100 through corporate actions and changes the *deliverable* instead — 5 shares after a 1-for-20 reverse split. Greeks scale with the deliverable, so 100 overstates that position exactly $20\times$. A Deribit BTC option is 1 BTC per contract.
- **Omitting the dividend yield.** Call delta carries a factor of $e^{-qT}$; assuming $q = 0$ on a yielding name biases delta by roughly $qT$ — small per position, systematic across the book, and always in the same direction.
- **Applying one underlying's tick to the whole position list.** An AAPL print repricing MSFT options at AAPL's spot produces Greeks that are not wrong by a little.
- **Letting a stale anchor survive a feed gap.** After a gap the anchor's provenance is unknown, and a stale anchor is worse than none: no anchor forces a reval, a stale one suppresses it. Reset on gap and on session boundary.
- **Applying an out-of-order tick.** A late-arriving earlier print advances the anchor backwards and leaves the book permanently mismarked. Reject it and resequence.
- **Publishing a delta outside $[-1, 1]$.** An impossible Greek downstream is not caught by anything that consumes it; the hedger will size against it.
- **Reading a fast refresh as a fresh number.** The output is only as current as the vol and expiry fed in. Stepping quickly off a stale surface is a stale risk number, delivered promptly.

## Verification

- **Closed form against hand-derived values.** At $S = K = 100$, $\sigma = 0.20$, $T = 1$, $r = q = 0$: $d_1 = +0.10$, $d_2 = -0.10$, $\Delta_{\text{call}} = 0.539827837$, $\Delta_{\text{put}} = -0.460172163$, $\Gamma = 0.019847627$, $\nu = 0.396952547$/vol pt, $\Theta = -0.010875412$/calendar day, price $= 7.965567455$.
- **Parity.** $\Delta_{\text{call}} - \Delta_{\text{put}} = e^{-qT}$ (not 1) once $q > 0$; $C - P = Se^{-qT} - Ke^{-rT}$.
- **First sight reprices.** A contract with no anchor $\implies$ `FULL_BLACK_SCHOLES` / `NO_ANCHOR`, and `spot_taylor_value_error_per_unit` is `None` — there is no truth to compare against and none is invented.
- **Taylor step.** Anchor at $S=100$, tick to $100.20$ ($+0.20\%$) $\implies$ `TAYLOR_EXPANSION`, $\Delta = 0.539827837 + 0.019847627 \times 0.20 = 0.543797363$, $\Gamma/\nu/\Theta$ unchanged, and the anchor still reads $S = 100$.
- **Anchor-drift regression (the critical one).** Anchor at $100$, tick to $100.40$ ($\implies$ Taylor, drift $0.40\%$), then to $100.80$ $\implies$ **`FULL_BLACK_SCHOLES` / `SPOT_MOVE_THRESHOLD`, drift $0.80\%$.** Measured against the previous tick the second step is $0.398\%$ and would wrongly step. Likewise 60 consecutive $+0.1\%$ ticks must produce $\ge 5$ revaluations; a last-tick baseline produces **zero** while spot travels $+6.18\%$.
- **Boundary.** Drift of exactly $0.5\%$ against a $0.5\%$ threshold steps; $0.6\%$ reprices. Down moves trigger symmetrically.
- **Non-spot triggers.** A $+1$ vol-point move at an unchanged spot $\implies$ `IV_MOVE_THRESHOLD` with drift $0.0$; a $61$s-old anchor on a flat tape $\implies$ `ANCHOR_AGE`; $T = 0.5/365 \implies$ `NEAR_EXPIRY` on any move; a step that puts a call delta above 1 $\implies$ `TAYLOR_DELTA_OUT_OF_BOUNDS` and a repriced delta $\le 1$.
- **Underlying isolation.** An AAPL tick against a book holding one AAPL and one MSFT leg $\implies$ one result, `positions_skipped_other_underlying == 1`, and no anchor created for the MSFT leg.
- **Negative checks.** A NaN/Inf/zero/negative spot, a blank symbol, a duplicate symbol in one tick, an out-of-order timestamp, `option_type="C"`, a string-typed number, a non-positive `multiplier`/`strike`/`implied_vol`/`time_to_expiry_years`, and a zero or negative config threshold must each raise. One bad leg must reject the whole tick and create no anchors.
- Run `python -m unittest discover -s skills/real-time-greeks-recalculation-on-market-moves/scripts` and confirm a 100% pass rate.

## Related Skills

- `options-greeks-real-time-portfolio-aggregation`
- `greeks-based-portfolio-hedging-automation`
- `options-implied-volatility-surface-construction`
- `options-pin-risk-management-at-expiry`
- `american-vs-european-style-option-exercise-handling`
- `options-chain-data-normalization-across-vendors`
- `multi-source-price-reconciliation-tie-breaking`
- `risk-metric-recalculation-frequency-tuning`
- `kill-switch-and-drawdown-circuit-breakers`
