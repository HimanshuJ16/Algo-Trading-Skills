---
name: supply-chain-data-for-earnings-prediction
description: >-
  Use when estimating a company's quarterly revenue growth from the reported results of
  firms one step up and down its supply chain, reading supplier growth through
  concentration weights and customer inventory as a drag.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: quant-research-alt-data
  tags: supply-chain, alternative-data, earnings-prediction, lead-lag, bullwhip-effect, earnings-surprise, point-in-time
  brokers_frameworks: "Alternative Data Pipeline; SEC EDGAR; Pandas; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when you are estimating a target company's quarterly **revenue** growth ahead of its earnings release, using the reported results of firms one step up and one step down its supply chain, and you need the estimate to be point-in-time defensible.

Two mechanisms are combined:

- **Upstream suppliers, positive.** A supplier's revenue is the target's purchase-order stream observed one step earlier in the chain, and suppliers frequently report before their customers. Cohen & Frazzini (2008) show news about an economically linked firm is not promptly incorporated by the market; Menzly & Ozbas (2010) show supplier and customer industries cross-predict each other's returns in both directions.
- **Downstream customer inventory, negative.** Inventory sitting at the target's customers is demand already shipped and not yet sold; it gets worked off by cutting the next order. Thomas & Zhang (2002) find the accrual anomaly is driven mainly by inventory changes, with inventory build predicting lower subsequent returns.

The blend weight on the supplier term is below 1.0 on purpose. Lee, Padmanabhan & Whang (1997) show order variance exceeds sales variance and the distortion *grows as one moves upstream* — the bullwhip effect — so a supplier's revenue swing is an amplified image of the target's end demand and must be damped when read back down the chain.

## When NOT to Use

- **Against an EPS consensus.** This engine produces a *revenue* growth estimate. Differencing it against an EPS expectation subtracts two different quantities and produces a number with no interpretation. Translating revenue into EPS needs incremental-margin and share-count assumptions this engine does not make. Version 1.0.0 made exactly that subtraction; that is the defect version 2.0.0 exists to fix, and the engine still cannot detect which consensus it was handed.
- **On a chain you have barely observed.** The public-filing graph is truncated: ASC 280-10-50-42 requires disclosure of the fact and amount of revenue from any customer at or above 10% of revenues but states the entity "need not disclose the identity of a major customer," and relationships below 10% need not be disclosed at all. The SEC's 2020 Regulation S-K modernization (Release 33-10825) then replaced the prescriptive requirement to name 10%+ customers in Item 101(c) with principles-based disclosure, so named counterparties became *scarcer* after 9 November 2020. Check `supplier_coverage_pct`, not the signal.
- **With a fixed dispersion.** `consensus_dispersion_pct` is required per call because it varies by name and by quarter. A hard-coded denominator produces a Z-score that only looks calibrated.
- **Where the supplier's growth is mostly other customers.** If the target is 2% of a supplier's revenue, that supplier's reported growth is noise about the target. The `min_read_through_share_pct` screen exists for this and should not be disabled to make a thin chain "work".
- **As a standalone entry signal.** The output is a directional bias on a fundamental estimate. `BUY_EARNINGS_SURPRISE` is not an order instruction; sizing, stops and exposure limits are owned elsewhere.
- **Against a sector move.** A supplier growing 20% in a sector growing 25% is a negative read-through that this engine scores positive. Industry and macro controls live upstream.
- **Where the vendor licence does not permit it.** Supply-chain relationship data is licensed, and what a vendor returns is a different question from what you may store or trade on — see `alternative-data-vendor-due-diligence-checklist` and `data-vendor-contractual-usage-restriction-tracking`.

## Prerequisites

- **`SupplierObservation` per upstream link**: `supplier_ticker`, `revenue_growth_pct`, `supplier_share_of_target_cogs_pct`, `target_share_of_supplier_revenue_pct`, `available_from_iso`.
- **`CustomerObservation` per downstream link**: `customer_ticker`, `inventory_growth_pct`, `customer_share_of_target_revenue_pct`, `available_from_iso`.
- **Availability timestamps, not period ends.** `available_from_iso` is the instant the figure became public — the earnings release or the SEC filing acceptance timestamp. These are far apart by construction: Form 10-Q is due 40 days after quarter end for large accelerated and accelerated filers and 45 days for everyone else; Form 10-K is due 60 / 75 / 90 days after year end for large accelerated / accelerated / non-accelerated filers. Timestamps must be timezone-aware ISO-8601; naive values are rejected rather than assumed UTC.
- **Two different concentration figures per supplier.** `supplier_share_of_target_cogs_pct` is the weight applied (how much of the target this supplier explains); `target_share_of_supplier_revenue_pct` is the ASC 280-style figure that governs how much of the supplier's growth is about the target at all. They are not interchangeable.
- **A revenue consensus and a dispersion in matching units.** `consensus_revenue_growth_pct` in the same period-over-period convention as the inputs, and a strictly positive `consensus_dispersion_pct` — either the cross-analyst dispersion of revenue estimates for this company-quarter, or the historical standard deviation of this model's own realized gap. Whichever you pick, `surprise_z_threshold` must be calibrated against the same one.
- **Thresholds you are willing to defend**: `supplier_blend_weight`, `inventory_blend_weight`, `surprise_z_threshold`, `min_supplier_coverage_pct`, `min_read_through_share_pct`, `max_observation_age_days`. The defaults are house values, not standards — see `references/standards.md`.

## Workflow

1. **Build the supply-chain graph and its weights**:
   - Map upstream suppliers and downstream customers, attaching both concentration figures to every edge.
   - **Decision point — record coverage, do not assume it.** The disclosed graph is truncated by the 10% ASC 280 threshold and by the post-2020 Item 101(c) shift to principles-based disclosure. Whatever share of the target's input spend you can actually observe is what `supplier_coverage_pct` will report, and it is the honest bound on the estimate.

2. **Align the periods, then stamp availability**:
   - Align supplier period $t-\tau$ to the target period $t$ before calling the engine. `lead_time_months` is echoed into the output for reproducibility; the engine cannot verify the alignment happened.
   - **Decision point — availability is a publication instant, never a period end.** A supplier's Q2 figure is not usable on 30 June; it is usable when the release crosses the wire, up to 40 or 45 days later.

3. **Apply the point-in-time cutoff**:
   - Pass `as_of_iso` on every call, in research and in production. Observations published after it are excluded and counted in `future_observations_excluded_count`.
   - **Decision point — set `max_observation_age_days` or accept that staleness is unbounded.** With it unset, `stale_observations_excluded_count` reads zero because nothing was checked, not because everything is fresh. The engine says so in `audit_notes`.

4. **Screen the read-through quality**:
   - **Decision point — a supplier below `min_read_through_share_pct` is dropped, not down-weighted.** Its total revenue growth is dominated by customers that are not the target, so including it at any weight injects unrelated demand into the estimate. The default floor of 10% is anchored to the ASC 280 materiality line by analogy, not by rule.

5. **Weight, blend, and compare against consensus**:
   - $\bar g_{\text{sup}} = \sum_i w_i g_i / \sum_i w_i$, $\bar h_{\text{cust}} = \sum_j v_j h_j / \sum_j v_j$.
   - $\text{Implied} = W_s \bar g_{\text{sup}} - W_c \bar h_{\text{cust}}$, defaults $W_s = 0.70$, $W_c = 0.30$.
   - $\text{Gap} = \text{Implied} - \text{Consensus revenue growth}$, then $Z = \text{Gap} / \sigma_{\text{consensus}}$.
   - **Decision point — normalizing by the observed weight total extrapolates.** It assumes the unobserved suppliers grew like the observed ones. That is the only assumption available, which is why coverage is reported and gated rather than hidden.

6. **Gate, then band**:
   - **Decision point — below `min_supplier_coverage_pct` there is no Z-score at all.** `surprise_z_score`, `implied_revenue_growth_pct` and `consensus_revenue_gap_pct` are `None` and the signal is `INSUFFICIENT_DATA`. `None` means "not measurable" and must never be rendered as 0.
   - **Decision point — `INSUFFICIENT_DATA` is not `NEUTRAL`.** `NEUTRAL` means the chain and the consensus were compared and agreed. A consumer may act on the second and must never act on the first.
   - Bands are decided on the **unrounded** $Z$ and are inclusive at the edge: $Z \ge$ `surprise_z_threshold` $\Rightarrow$ `BUY_EARNINGS_SURPRISE`; $Z \le -$`surprise_z_threshold` $\Rightarrow$ `SELL_EARNINGS_DISAPPOINTMENT`; otherwise `NEUTRAL`.

7. **Persist the audit record with its configuration**: the Z-score is not reproducible without the thresholds that produced it.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Differencing a revenue estimate against an EPS consensus**: the two are not the same quantity, and the result still looks like a well-formed percentage. This was version 1.0.0's behaviour.
- **Using the fiscal period end as the availability date**: a 10-Q lands up to 40 or 45 days after quarter end and a 10-K up to 60, 75 or 90 days after year end. Treating the period end as usable dates the whole backtest wrong in the direction that flatters it.
- **Ignoring the bullwhip effect**: reading a supplier's revenue swing straight through to the target overstates end demand, because order variance amplifies as it moves upstream. It also runs the other way — treating customer inventory build as evidence of strong demand reads the leading indicator of an order cut as a positive.
- **Weighting a 2%-of-COGS supplier like a sole-source one**: an unweighted mean over disclosed links is dominated by whichever links the vendor happened to capture, not by the ones that matter.
- **Reading a supplier's total growth as growth from the target**: if the target is 3% of that supplier's book, the number is about somebody else's demand.
- **Treating truncated coverage as full coverage**: ASC 280 requires no disclosure below 10% and no customer identity at all, and Regulation S-K Item 101(c) stopped prescribing 10%+ customer names in 2020. A graph that looks complete is not.
- **A hard-coded dispersion**: dividing every gap by the same assumed standard deviation makes the Z-score a rescaled gap wearing a Z-score's clothes.
- **A dispersion in the wrong units**: growth rates in percentage points ($5.0$) against a dispersion in decimals ($0.05$) inflates every $Z$ by 100 and still returns a plausible-looking number. The engine validates that the dispersion is positive and finite; it cannot check its units.
- **Absorbing NaN into NEUTRAL**: `nan >= 1.0` and `nan <= -1.0` are both False, so an unguarded banding chain reports a confident NEUTRAL on missing data. The engine raises instead.
- **Rounding before banding**: `round(0.99996, 4) == 1.0` promotes NEUTRAL to a directional signal on a value the data does not support.
- **Double-counting a link**: the same supplier appearing twice in a batch double-weights it in the concentration mean. The engine raises on duplicate tickers.

## Verification

- Instantiate `SupplyChainDataForEarningsPredictionEngine()`. One supplier at $+25\%$ revenue growth (60% of target COGS, target is 20% of the supplier) and one customer at $+5\%$ inventory growth (40% of target revenue), against a $10\%$ revenue consensus with dispersion $5\%$: implied $= 0.70 \times 25 - 0.30 \times 5 = 16.0\%$, gap $= 6.0\%$, $Z = 1.2$, `BUY_EARNINGS_SURPRISE`.
- Supplier at $-10\%$ with customer inventory $+20\%$ against a $5\%$ consensus: implied $= -13.0\%$, gap $= -18.0\%$, $Z = -3.6$, `SELL_EARNINGS_DISAPPOINTMENT`.
- Concentration weighting: a 60%-of-COGS supplier at $+20\%$ and a 20%-of-COGS supplier at $0\%$ give a weighted growth of $(60 \times 20 + 20 \times 0)/80 = 15.0\%$, not the unweighted $10.0\%$.
- Dispersion is read: the same $6.0\%$ gap against a dispersion of $2.0$ gives $Z = 3.0$, not $1.2$.
- Banding: $Z$ of exactly $\pm 1.0$ is directional at the inclusive edge; $Z = 0.99996$ stays `NEUTRAL` despite rounding to $1.0$ at four decimals; a `surprise_z_threshold` of $2.0$ makes $Z = 1.2$ `NEUTRAL`.
- Point-in-time: an observation stamped one second after `as_of` lands in `future_observations_excluded_count`; one stamped exactly at `as_of` is retained; `2026-08-01T09:00:00-04:00` is after a `12:00Z` cutoff; naive `as_of` and naive `available_from_iso` both raise.
- Screens: `target_share_of_supplier_revenue_pct` of $9.99$ is excluded and $10.0$ is retained; `supplier_coverage_pct` of $9.9$ yields `INSUFFICIENT_DATA` and $10.0$ is measurable.
- `INSUFFICIENT_DATA` returns `surprise_z_score is None`, `is_signal_measurable is False`, and logs a WARNING.
- Negative checks: NaN or infinite growth, a non-numeric growth, a weight outside $[0, 100]$, weights summing above $100\%$, a zero/negative/NaN dispersion, duplicate tickers, a wrong observation type, and an empty `target_asset` all raise `ValueError`. A version 1.0.0 positional call raises `TypeError`.
- Run `python -m unittest discover -s skills/supply-chain-data-for-earnings-prediction/scripts` and confirm a 100% pass rate.

## Related Skills

- `point-in-time-fundamentals-data-joins`
- `lookahead-bias-elimination`
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
- `alternative-data-feature-integration`
- `alternative-data-vendor-due-diligence-checklist`
- `data-vendor-contractual-usage-restriction-tracking`
- `insider-trading-controls-for-alternative-data-usage`
- `credit-card-transaction-data-signal-construction`
- `earnings-call-transcript-nlp-signal-research`
