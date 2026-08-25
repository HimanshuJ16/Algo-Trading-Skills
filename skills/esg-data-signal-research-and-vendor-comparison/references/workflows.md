# Workflows for ESG Data Signal Research and Vendor Comparison

## 1. Vendor rating ingestion

1. Pull each vendor's assessment in **vendor-native units**. Do not pre-scale — the engine's normalization is anchored to each vendor's published band structure and a pre-scaled input silently defeats it.
2. Stamp every record with a timezone-aware `as_of` vintage. This is the date the snapshot was *observed*, not the vendor's stated rating date; vendors restate history under both labels.
3. Map identifiers before joining. Vendor panels disagree on tickers for dual listings and post-corporate-action names — see `reference-data-symbol-mapping-across-vendors`.
4. Distinguish three states explicitly:
   - **Covered** — a value in range.
   - **Not covered** — `None`. Legitimate; the issuer simply drops out of the consensus for that vendor.
   - **Malformed** — NaN, infinity, out-of-range, or an unrecognised MSCI token. This is a feed defect and raises. Never coerce it into "not covered", or a broken pipeline becomes indistinguishable from thin vendor coverage.

## 2. Normalization

| Vendor | Transform | Why not the obvious alternative |
|---|---|---|
| MSCI | Band mid-point, $(2k+1)/14$ for $k=0$ (`CCC`) … $6$ (`AAA`) | The letter identifies an interval of the 0–10 Industry-Adjusted Score. End-point mapping ($\text{AAA}=1.0$) asserts the top of that interval for every AAA issuer and inflates tail dispersion by ~7 points on each side. |
| Sustainalytics | $1.0 - \dfrac{\min(\text{Risk},\,40)}{40}$ | The Severe band starts at 40 and is open-ended, so 100 is a nominal ceiling nobody reaches. Dividing by 100 maps a Severe issuer at 45 to $0.55$ and puts the laggard band out of reach at any realistic risk score. |
| LSEG/Refinitiv | $\dfrac{\text{Score}}{100}$ | Already reported on the target scale. |

If your research requires genuine cross-vendor comparability rather than a common axis, replace this step: rank each vendor's raw scores **within the traded universe on a common date** and use those percentiles. Pass them through `msci_rating_map` or by pre-normalizing, and set `sustainalytics_severe_threshold` accordingly. Nominal rescaling cannot remove the scope difference between an industry-relative rating and an absolute one.

## 3. Consensus and dispersion

1. Collect the $K$ covering vendors.
2. $\bar{S} = \frac{1}{K}\sum S_k$.
3. $\sigma_{\text{esg}} = \sqrt{\frac{1}{K}\sum (S_k - \bar{S})^2}$ — population form, deviations taken about the **unrounded** mean. Rounding the mean first biases the variance upward.
4. Leave both `None` where they are undefined: $\bar{S}$ with $K=0$, $\sigma_{\text{esg}}$ with $K=1$. Do not substitute $0.0$.
5. Note the $K=2$ property when reading the threshold: with two vendors the population dispersion is $|S_1 - S_2|/2$, so $\sigma_{\text{esg}} > 0.25$ requires the two normalized scores to differ by more than $0.5$. The same threshold is materially easier to trip with three vendors.

## 4. Exclusion and coverage audit

1. Resolve rule-based exclusions **upstream** — revenue thresholds, treaty lists, UNGC/OECD violation status. The engine records them; it does not evaluate them.
2. Pass them in via `has_controversial_weapons` and/or `exclusion_reasons`. Reasons are upper-cased, trimmed and de-duplicated, preserving order.
3. An excluded issuer keeps its normalized per-vendor scores and consensus in the report. That is deliberate: the audit trail must survive on exactly the records most likely to be reviewed. The `signal` and `exclusion_reasons` fields are what downstream consumers must gate on — never the consensus score.
4. Enforce the coverage floor before any directional call. `min_vendors_for_conviction` defaults to 2.

## 5. Signal generation

Precedence, first match wins:

1. `EXCLUDED_SECTOR`
2. `INSUFFICIENT_VENDOR_COVERAGE`
3. `NEUTRAL_HIGH_DISAGREEMENT`
4. `BULLISH_ESG_LEADER` — $\bar{S} \ge$ `leader_threshold`
5. `BEARISH_ESG_LAGGARD` — $\bar{S} \le$ `laggard_threshold`
6. `NEUTRAL`

The disagreement gate sits above **both** directional branches. An asymmetric gate — leader gated, laggard not — lets an issuer rated `CCC` by one vendor and 80/100 by another emit a confident short-side ESG signal off maximal disagreement.

## 6. Backtest integration

1. Store vendor scores in a point-in-time table keyed on `(identifier, vendor, as_of)` and query as-of the trading date. See `point-in-time-fundamentals-data-joins`.
2. Never rebuild history from a current vendor extract. Median rewritten Refinitiv ESG scores were 18% below the original vintage, and quantile membership changed with them (see `references/standards.md`).
3. Build the universe from a point-in-time constituent list, not today's vendor coverage — see `survivorship-bias-free-universe-construction`.
4. Check for methodology-version breaks inside the backtest window and treat each side of a break as a separate variable. A vendor replacing its model mid-sample is not a data update.
5. Re-estimate `leader_threshold`, `laggard_threshold` and `disagreement_threshold` per universe out-of-sample. The defaults are illustrative.
