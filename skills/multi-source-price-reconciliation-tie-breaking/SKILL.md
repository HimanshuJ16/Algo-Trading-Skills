---
name: multi-source-price-reconciliation-tie-breaking
description: >-
  Use when three or more vendors quote the same instrument and one canonical price must
  come out, with median-distance outlier attribution and a tick-size-aware agreement
  tolerance. Two sources cannot break a tie.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: data-management-global, price-reconciliation, tie-breaking, multi-vendor, outlier-attribution, canonical-price, independent-price-verification, data-quality
  brokers_frameworks: "SEC Reg NMS Rule 612 (17 CFR 242.612, minimum pricing increment); SEC Rule 2a-5 (17 CFR 270.2a-5, fair value and pricing service oversight); CRR Article 105(8) (Regulation (EU) No 575/2013, independent price verification); Python standard library (statistics, dataclasses, enum)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when **three or more independent sources** quote the same instrument — Bloomberg against Refinitiv/LSEG against Polygon, Binance against Coinbase against Kraken, an exchange feed against two aggregators — and one number has to come out the other end: a mark, a signal input, a risk valuation, a limit price.

The component answers two questions that are routinely collapsed into one:

> Is there a price we can use, and did anything independent actually corroborate it?

A price picked by a tie-breaker *because the vendors disagreed* is usable and uncorroborated. A price averaged from three vendors that agreed to within a tick is corroborated. A pipeline that reports both as "success" has thrown away the only information that made running three feeds worth paying for. `MultiSourcePriceReconcilerEngine` therefore returns a `status` **and** an `is_cross_verified` flag, and they are not the same claim.

## When NOT to Use

- **With exactly two sources.** Outlier rejection here is a distance test against the median, and with two quotes the median sits exactly midway, so both are always equidistant — either both pass or both fail, and no outlier is ever attributable. The engine detects this and skips filtering rather than pretending. Two-source arbitration with latency-skew handling and quarantine hysteresis is `market-data-feed-arbitration-across-vendors`.
- **As a failover controller.** This reconciles the quotes it is handed. Deciding which vendor to subscribe to when one dies, and how long to wait before promoting a backup, is `vendor-outage-fallback-data-source-hierarchy`.
- **Across unlike price bases or currencies.** One vendor's last trade against another's quote midpoint diverges by roughly half a spread *permanently*, and the engine will dutifully report a standing divergence forever. Normalise upstream: `multi-exchange-feed-normalization`, `currency-pair-quoting-convention-normalization`.
- **For instruments that can price at or below zero.** Percentage-of-median deviation is undefined there, so the engine rejects non-positive quotes outright. Negative-carry calendar spreads and the April 2020 WTI settlement need an absolute-difference reconciliation instead.
- **As a risk control.** It emits a status; it stops nothing. Wire `is_cross_verified is False` into `graduated-response-to-data-quality-degradation` or `kill-switch-and-drawdown-circuit-breakers` to make it act.
- **As a published benchmark.** If the reconciled price is distributed to third parties or referenced by a financial instrument, benchmark-administration obligations may attach — see `eu-benchmark-regulation-for-strategies-referencing-indices`.

## Prerequisites

- **Three or more genuinely independent vendors** for the symbol, on the **same price basis** in the same currency, each separately entitled for the intended use. Two aggregators that both resell the same underlying feed are one source wearing two badges, and they will agree with each other while being wrong together.
- **A single local receipt clock.** `timestamp` and `as_of` are local receipt times, never vendor- or exchange-supplied event times. Staleness is a duration; measuring it across two vendors' clocks measures their skew (`clock-skew-correction-for-tick-timestamps`).
- **A tolerance calibrated from recorded cross-vendor history** for this exact vendor set and instrument, **floored at one minimum price increment**. Under Reg NMS Rule 612 an NMS stock quoted at or above $1.00 currently moves in $0.01 increments, so one tick exceeds 5 bps for any stock under $20 — the default `tolerance_pct=0.0005` is narrower than a single lawful penny across most of that universe.
- **A caller that branches on `status`.** `canonical_price` is `Optional[float]` and is `None` when every quote is stale.

## Workflow

1. **Reject the batch before pricing it.**
   - Non-finite and non-positive prices are rejected at construction. `NaN` fails every comparison (`abs(nan - m)/m > bound` is `False`), so an unchecked `NaN` passes the outlier filter untouched and is published as the canonical price.
   - **Decision point:** a quote whose `symbol` differs from the batch symbol, or a repeated `vendor_id`, raises. Both are dispatcher bugs, and both produce a *confident* wrong answer — the second by reporting a quorum of independent sources that does not exist.

2. **Gate on staleness before any arithmetic.**
   - **Decision point:** a stale quote inside the outlier bound cannot be filtered out later. A vendor frozen 100 seconds ago at 0.8% away survives a 1% deviation test, widens the observed spread past tolerance, and converts two fresh vendors agreeing to 2 bps into an unresolved divergence.
   - `as_of` is required, never defaulted to the freshest quote in the batch: that default makes the newest quote zero seconds old by construction and can therefore never detect the outage where every vendor has stopped updating.
   - Every quote stale → **no price**. `canonical_price` is `None`, not the last good value.

3. **Attribute outliers only with enough sources.**
   - With fewer than three usable quotes, skip the filter and say so. Do not run it and report "0 outliers" — that reads on a dashboard as *checked and clean*.
   - Reject quotes further than `max_deviation_pct` from the median of the usable quotes, with the bound floored at one tick.

4. **Handle the deadlock the filter can produce.**
   - **Decision point:** with an even number of quotes split into two clusters (100, 100, 105, 105) the median falls in the gap and *every* quote fails. That is a deadlock, not a detection. Retain the quotes, mark the result unresolved and unverified, and never silently reset the rejection list to empty — an audit trail that says "4 valid, 0 outliers" when all four were rejected is worse than no audit trail.

5. **Audit agreement against the surviving quotes.**
   - Recompute the median over the **survivors**; the all-quotes median is still contaminated by whatever was just removed.
   - Effective tolerance is `max(tolerance_pct, one_tick / median)`. Do not calibrate to the last bit: a decimal threshold such as 0.0005 is not exactly representable in binary, so exact-boundary behaviour is a coin flip.

6. **Composite only when they actually agree.**
   - Within tolerance and two or more survivors → reliability-weighted average, `is_cross_verified=True`. This is the only cross-verified outcome.
   - Exactly one survivor → the price is usable but `RECONCILIATION_UNCORROBORATED`.

7. **Tie-break deterministically, and call it policy.**
   - `PRIORITY` (lowest rank), `FRESHNESS` (newest), `VOLUME_WEIGHTED` (deepest book). Every key is a **total order ending in `vendor_id`**, so the winner never depends on the caller's list order.
   - **Decision point:** a tie-broken price is `RECONCILIATION_UNRESOLVED` with `is_cross_verified=False`. Preferring Bloomberg when the vendors disagree is an operator *preference*, not a detection of who was wrong.
   - A misspelt method raises. It must not degrade to "first quote in the list" — order-dependent pricing is exactly the non-determinism this skill exists to prevent.

8. **Emit the audit record.** `PriceReconciliationReport` carries the surviving quotes, the rejected quotes, the stale quotes, the observed spread, the effective tolerance actually applied, and the contributing vendor ids.

> Full procedure: see `references/workflows.md`.
> Standards and sourced citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **`NaN` as a canonical price.** Every threshold comparison against `NaN` is `False`, so a corrupt quote is classified *valid*, flows into the weighted average, and poisons the output — which then compares `False` against every downstream risk limit too.
- **Silently emptying the rejection list.** Falling back to "use all quotes" when the filter rejects everything is a reasonable *pricing* choice and a falsified *audit* record. Keep the fallback; report it as unresolved.
- **Reporting zero outliers when the filter never ran.** With two sources the filter is structurally incapable of attributing one. "0 outliers" and "not checked" must not render identically.
- **Stale quotes inside the deviation band.** A frozen vendor 0.8% away survives a 1% filter, and no amount of outlier logic will catch it. Only a receipt-clock age check will.
- **Substituting the last known price on a blackout.** The one moment every vendor is silent is the one moment a cached price is most likely to be wrong and most likely to be traded on.
- **Tolerances below one tick.** 5 bps on a $12 stock is 60% of a single lawful $0.01 increment, so every legal one-cent disagreement raises a divergence — a permanent false-alarm generator across most of the sub-$20 universe.
- **Duplicate vendor ids.** The same feed delivered twice — a replayed message, a double subscription, a symbol-mapping collision — biases the median toward itself, double-weights the composite, and turns "3 sources agreed" into "2 sources, one counted twice".
- **Order-dependent tie-breaks.** `min()` over a key that leaves ties returns whichever element came first. Build the quote list from a `dict` and the canonical price becomes irreproducible run to run, which is unauditable regardless of whether it was right.
- **Blending disagreeing vendors.** `WEIGHTED_AVERAGE` across quotes that breach tolerance manufactures a price no vendor quoted and no venue ever showed, and it *looks* more authoritative than either input.
- **Hardcoded rounding.** `round(price, 4)` silently returns `0.0` for a token quoted at 0.00002181 — a total loss of the price, not a loss of precision.
- **Treating divergence as proof of a bad vendor.** Cross-vendor disagreement is dominated by relative latency. In adopting the Market Data Infrastructure rule the SEC described the structural gap in which proprietary-feed subscribers "receive more content-rich data faster" than consolidated-tape consumers. A feed that is merely *ahead* is not an outlier.

## Verification

- Feed Bloomberg $100.00 (priority 1), Refinitiv $100.02 (priority 2) and a bad Polygon $105.00 and confirm Polygon is attributed as the outlier (4.98% from a median of $100.02), the survivors agree within tolerance, and the composite is $100.01 with `status == "RECONCILIATION_SUCCESS"` and `is_cross_verified is True`.
- Submit `NaN`, `inf`, `0.0` and a negative price and confirm each raises at quote construction.
- Submit 100, 100, 105, 105 and confirm `filter_deadlocked is True`, `status == "RECONCILIATION_UNRESOLVED"`, and that the report does **not** claim four valid quotes and zero outliers as a success.
- Submit two quotes and confirm no outlier is attributed and the result is not cross-verified.
- Submit a quote for a different symbol, and a repeated `vendor_id`, and confirm both raise.
- Age every quote past `max_quote_age_seconds` and confirm `canonical_price is None` with `status == "RECONCILIATION_NO_USABLE_QUOTE"`.
- Rotate the quote list and confirm both the tie-break winner and the composite price are bit-identical across every rotation.
- Reconcile two identical 0.00002181 quotes and confirm the canonical price is not `0.0`.
- Run `python -m unittest discover -s skills/multi-source-price-reconciliation-tie-breaking/scripts` and confirm a 100% pass rate.

## Related Skills

- `market-data-feed-arbitration-across-vendors`
- `vendor-outage-fallback-data-source-hierarchy`
- `cross-vendor-timestamp-precision-reconciliation`
- `clock-skew-correction-for-tick-timestamps`
- `reference-data-golden-source-designation`
- `multi-exchange-feed-normalization`
- `graduated-response-to-data-quality-degradation`
- `data-vendor-cross-validation-for-backtests`
- `market-data-latency-monitoring-per-vendor`
