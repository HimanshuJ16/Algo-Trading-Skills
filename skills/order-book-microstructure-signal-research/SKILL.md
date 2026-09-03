---
name: order-book-microstructure-signal-research
description: >-
  Use when testing whether a top-of-book signal genuinely leads the mid-price, computing
  order flow imbalance to the published definition including both queue-depletion
  branches. A windowed research audit, not a live generator.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: microstructure, ofi, order-flow-imbalance, micro-price, depth-imbalance, hft-signals, quant-research, information-coefficient
  brokers_frameworks: "Cont/Kukanov/Stoikov (2014) OFI; Stoikov (2018) Micro-Price; Level 1 / Top-of-Book Quote Data; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when you have a time-ordered series of top-of-book quotes for one instrument and need a defensible answer to *does this microstructure signal lead the mid-price, or does it only look like it does?*

Order Flow Imbalance is the right variable to ask that of, because it treats a market sell and a cancelled buy of the same size as the same event — both remove the same depth from the bid queue — and so captures supply/demand pressure that trade-based measures miss. Cont, Kukanov and Stoikov found that in TAQ data for 50 randomly selected S&P 500 constituents over April 2010, aggregated OFI explains mid-price changes linearly with an **average $R^2$ of 65%**, with a slope inversely proportional to market depth.

Read that number carefully, because it is the single most misused result in this area: **it is contemporaneous.** Their regression is $\Delta P_k = \beta\,OFI_k + \epsilon_k$ over *the same* 10-second interval. It says order flow and price move together, which is nearly a description of how a matching engine works. It says nothing about whether OFI at time $t$ predicts the return from $t$ to $t+k$. That forward question is what this engine measures, and the ICs it returns will be far smaller than 0.65.

The engine reports the forward-return Information Coefficient for OFI, for the weighted-mid deviation and for depth imbalance, a hit ratio over directional calls only, and a t-statistic computed on the non-overlapping effective sample.

## When NOT to Use

- **As a live signal generator.** This is a windowed, after-the-fact research audit over a captured series. It holds the whole series in memory, validates it end to end before computing anything, and has no incremental update path. For the fast-path production version see `order-book-imbalance-signal-pipeline`.
- **To certify a signal on a short capture.** An approval requires at least 30 *non-overlapping* observations. At a 5-tick horizon that is 150+ ticks of usable series, and 150 ticks is still a tiny window in microstructure terms. Below the gate the verdict is `INSUFFICIENT_SAMPLES`, which is not the same statement as `WEAK_SIGNAL`.
- **On depth beyond level 1.** Every formula here reads only the best bid and best ask. On a thin book the top level is a poor proxy for real available liquidity — see `order-book-depth-processing-l2-l3`.
- **On a consolidated or NBBO feed you have not cleaned.** Quotes from different venues arrive out of order and can cross. The engine rejects a crossed book rather than computing through it, because on a crossed tick the weighted-mid deviation silently inverts sign relative to the depth imbalance.
- **On negatively-priced instruments.** Simple mid-to-mid returns are undefined through zero and sign-flip through a negative denominator. CME WTI settled below zero on 2020-04-20; such a series needs a log or absolute-change return convention this module does not implement, and it is rejected rather than mispriced.
- **As a substitute for a transaction-cost model.** A positive IC over 5 ticks is not an edge. The move it predicts is a fraction of a tick, and crossing the spread costs a whole one. `transaction-cost-analysis-tca-integration` and `queue-position-modeling-for-passive-orders` decide whether a statistically real signal is economically tradable.

## Prerequisites

- A time-ordered top-of-book series (`timestamp_ns`, `symbol`, `bid_price`, `bid_qty`, `ask_price`, `ask_qty`), single instrument, sorted, uncrossed, finite, positive prices, non-negative sizes. The engine enforces all of this and rejects rather than repairs.
- A forward horizon $k \ge 1$ in ticks. A horizon of 0 makes every forward return identically zero; a negative one silently converts the study into a look-back and manufactures correlation.
- Optional `ofi_window_ticks`: the number of consecutive event contributions summed into the tested signal. The default of 1 tests the per-event contribution $e_n$; values above 1 reproduce the interval-summed $OFI_k$ of the published work.

## Workflow

1. **Compute $e_n$ with all six branches, not four.** Cont/Kukanov/Stoikov define the contribution of the $n$-th top-of-book event as
   $$e_n = \mathbb{1}_{\{P^B_n \ge P^B_{n-1}\}} q^B_n - \mathbb{1}_{\{P^B_n \le P^B_{n-1}\}} q^B_{n-1} - \mathbb{1}_{\{P^A_n \le P^A_{n-1}\}} q^A_n + \mathbb{1}_{\{P^A_n \ge P^A_{n-1}\}} q^A_{n-1}$$
   which unrolls to six cases. The two that get dropped are the queue-depletion ones: when the best **bid price falls** the contribution is $-q^B_{n-1}$, and when the best **ask price rises** it is $+q^A_{n-1}$ — the size that was removed by a market order or a cancellation. A prior revision of this skill assigned $0$ to both, so a book whose entire bid queue was swept registered an imbalance of zero.
2. **Treat $e_0$ as undefined, not zero.** The first row has no predecessor to difference against. It is emitted with `is_event_observed=False` and excluded from the research sample; admitting it puts a fabricated observation into the IC.
3. **Aggregate if you want the published variable.** $e_n$ is one event's contribution; $OFI_k$ is the *sum* over an interval, and it is the sum that the literature regresses against price changes. Set `ofi_window_ticks` above 1 and the engine drops the warm-up rows, because a partial window is a different random variable from a full one.
4. **Know that the weighted mid is not an independent second signal.** The engine computes $P_w = \frac{q^B P^A + q^A P^B}{q^B + q^A}$, and exactly
   $$P_w - \frac{P^B + P^A}{2} = \frac{VOI}{2} \cdot (P^A - P^B)$$
   so `micro_price_dev` is `voi` rescaled by the spread. On a constant-spread instrument the two ICs are *arithmetically the same number*; the engine raises `CONSTANT_SPREAD_COLLINEARITY` when it detects this, and reports `ic_voi_forward_return` alongside so the duplication is visible rather than inferred.
5. **Compute returns at full precision on both endpoints.** Rounding the current mid while leaving the forward mid unrounded biases every return asymmetrically, and a 4-decimal round erases the signal entirely on a 5-decimal FX cross. Features carry unrounded values; rounding happens only on the report.
6. **Score the hit ratio over directional calls only.** A zero signal is not a prediction and a zero forward return is not an outcome. Both are excluded from the numerator *and* the denominator, and counted in `flat_or_neutral_ticks`. Read that count: a hit ratio of 89% over 48 directional calls out of 397 observations is a statement about 12% of the sample.
7. **Discount for overlap before believing the t-statistic.** A $k$-tick forward return sampled every tick shares $k-1$ ticks with its neighbour. Grinold's $IR = IC\sqrt{BR}$ counts *independent* decisions; the naive t-statistic counts all of them and is inflated by roughly $\sqrt{k}$. The engine reports the t-statistic on `observations // k` and refuses to certify below `MIN_EFFECTIVE_OBSERVATIONS`.
8. **Read a strongly negative IC as a bug report.** The published model predicts a *positive* coefficient on OFI. A materially negative IC raises `IC_SIGN_INVERTED`, and the first three things to check are a bid/ask column swap on feed load, a sign convention on the quantity field, and a timestamp misalignment — not a contrarian edge.

> Full procedure: see `references/workflows.md`.
> Sources, formula provenance, and what nobody publishes: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Dropping the queue-depletion branches of $e_n$.** Assigning 0 when the bid price falls or the ask price rises looks harmless because the price-unchanged branch — which is correct — is by far the most frequent. The signal still tracks the book most of the time, so nothing about the output looks wrong; it is exactly the violent depletion events, the ones that carry the most information, that are silently zeroed.
- **Quoting the 65% $R^2$ as evidence of predictive power.** It is a contemporaneous same-interval regression. Reporting it in support of a forward-return strategy is the single most common misuse of this literature, and no forward IC this engine produces will come close to it.
- **Reading `ic_micro_price_dev_return` as corroboration of `ic_voi_forward_return`.** They are the same quantity scaled by the spread. On a one-tick-spread name they are equal to four decimal places, and treating them as two agreeing signals double-counts one.
- **Calling the weighted mid "the micro-price".** Stoikov's micro-price is a martingale limit of expected future mid-prices; the weighted mid is the noisier, non-martingale approximation it was built to improve on. This engine computes the weighted mid. The field is named `micro_price` for backward compatibility, and that name overstates what it is.
- **Counting non-events as hits.** A prior revision scored every `(OFI == 0, return == 0)` tick as correct, so a completely static book reported a perfect hit ratio. In tick data the top of book is unchanged far more often than not, so this inflates the ratio with exactly the ticks where nothing was predicted and nothing happened.
- **Believing a t-statistic computed on overlapping returns.** Overlapping k-period returns mechanically accumulate autocorrelation. The fix here — dividing by $k$ — is deliberately conservative and is *not* a HAC estimator; if you need the information in the overlapping samples rather than discarding it, use Newey-West or Hansen-Hodrick standard errors, noting that both are known to be biased downwards when the horizon is long relative to the sample.
- **Letting a NaN into the series.** NaN does not raise, does not sort, and compares `False` against every threshold. It propagates a NaN IC, `NaN >= 0.05` evaluates `False`, and a corrupted capture reports a clean `WEAK_SIGNAL` verdict as if it had been measured.
- **Rounding a signal inside the data structure.** OFI rounded to 2 decimals is identically zero for an instrument quoted in fractional units; a mid rounded to 4 is identically constant for a 5-decimal FX cross. Round for display, never for computation.
- **Certifying alpha on a handful of independent observations.** Twenty ticks at a 2-tick horizon give roughly 8 non-overlapping observations. An IC point estimate there is noise with a decimal point, and `INSUFFICIENT_SAMPLES` is the honest verdict rather than an approval or a rejection.
- **Researching a horizon shorter than your round trip.** A signal that leads price by 2 ticks is unreachable if your tick-to-trade path is longer than the interval between ticks. Establish the latency budget first — see `tick-to-trade-latency-measurement`.
- **Treating the thresholds as standards.** `MIN_IC_FOR_ALPHA = 0.05`, `MIN_HIT_RATIO_PCT = 53.0` and `MIN_EFFECTIVE_OBSERVATIONS = 30` are this skill's engineering choices. No regulator, exchange or standards body publishes any of them.

## Verification

- Feed a two-tick series where the best bid falls from 150.00 (size 900) to 149.90 (size 10) with the ask unchanged $\implies$ verify `ofi == -900.0`. The prior revision returned `0.0` here.
- Feed a two-tick series where the best ask rises from 150.10 (size 800) to 150.20 (size 5) with the bid unchanged $\implies$ verify `ofi == +800.0`.
- For any book, verify the identity `micro_price_dev == (voi / 2) * (ask_price - bid_price)` to within float tolerance.
- Feed a completely static book $\implies$ verify `directional_predictions == 0`, `hit_ratio_pct == 0.0` and finding `ZERO_VARIANCE_SIGNAL`; a static book must not score 100%.
- Swap only the `bid_qty` and `ask_qty` columns of a series that certifies $\implies$ verify the IC turns negative and `IC_SIGN_INVERTED` is raised.
- Verify `effective_observations == observations // forward_horizon_ticks`, and that a 20-tick series returns `INSUFFICIENT_SAMPLES` rather than a verdict.
- Run `python -m unittest discover -s skills/order-book-microstructure-signal-research/scripts`.

## Related Skills

- `order-book-imbalance-signal-pipeline`
- `order-book-depth-processing-l2-l3`
- `microstructure-noise-filtering-for-hf-signals`
- `factor-research-multiple-testing-correction`
- `lookahead-bias-elimination`
- `queue-position-modeling-for-passive-orders`
- `adverse-selection-measurement-for-passive-orders`
- `tick-to-trade-latency-measurement`
