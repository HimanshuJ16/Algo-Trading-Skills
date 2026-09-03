# Workflows for Supply Chain Data for Earnings Prediction

The engine is split by *what each step can know*. Graph construction, fiscal-period
alignment and consensus sourcing happen upstream and cannot be verified from inside
the module; availability cutoffs, concentration weighting, screening and
standardization happen inside it and are enforced.

## 1. Supply-chain graph construction (upstream, not in this module)

Map the target's upstream suppliers and downstream customers, attaching **two**
distinct concentration figures to every supplier edge:

| Figure | Meaning | Used for |
|---|---|---|
| `supplier_share_of_target_cogs_pct` | How much of the *target's* input spend this supplier accounts for | The weight actually applied |
| `target_share_of_supplier_revenue_pct` | How much of the *supplier's* revenue comes from the target — the ASC 280-style figure | The read-through screen |

They answer different questions and are not interchangeable. A supplier can be 40%
of the target's bill of materials while the target is 2% of that supplier's book;
its reported growth is then economically important to the target and almost
entirely uninformative about it.

Customer edges carry one figure, `customer_share_of_target_revenue_pct`.

**Coverage is not a formality.** The filings-derived graph is truncated: ASC
280-10-50-42 requires no disclosure below 10% of revenues and does not require the
customer's identity at all, and SEC Release 33-10825 (effective 9 November 2020)
replaced Regulation S-K Item 101(c)'s prescriptive naming of 10%+ customers with
principles-based disclosure. Whatever share of the target's input spend you can
observe is what `supplier_coverage_pct` will report.

## 2. Lead-lag alignment (upstream, echoed but not verified)

- Align supplier period $t-\tau$ to the target period $t$ before calling the engine.
  The mechanism that makes the lead real is usually a **reporting-calendar** offset —
  a supplier on a September quarter-end reports in October, ahead of a target on a
  December quarter-end — not a physical shipping lag.
- `lead_time_months` is a constructor parameter recorded in the output as
  `declared_lead_time_months`, purely so a stored signal can be replayed. The engine
  has no way to check that the caller actually applied it.
- Confirm the growth rates are like-for-like: organic vs. reported, constant vs.
  current currency, and 52/53-week retail calendars each change the number and none
  of them are visible to the engine.

## 3. Availability stamping and the point-in-time cutoff

- `available_from_iso` is the instant the figure became **public** — the earnings
  press release or the SEC filing acceptance timestamp — never the fiscal period end.

  | Report | Large accelerated | Accelerated | Non-accelerated |
  |---|---|---|---|
  | Form 10-Q | 40 days | 40 days | 45 days |
  | Form 10-K | 60 days | 75 days | 90 days |

  (Days after period end; Exchange Act Rule 12b-2 filer categories.) Using the period
  end as the usable date advances every observation by up to a quarter, always in the
  direction that flatters the backtest.
- Timestamps must be timezone-aware. `2026-08-01T09:00:00-04:00` is `13:00Z` and
  therefore *after* a `12:00Z` cutoff; a naive string is refused rather than assumed
  to be UTC.
- `as_of_iso` is required on every call. Observations published after it are excluded
  and counted in `future_observations_excluded_count`. `as_of` also stamps the output,
  so two identical calls produce identical records.
- `max_observation_age_days` bounds staleness. The retained window is
  $[\,as\_of - \text{age},\ as\_of\,]$, inclusive at both ends. Left `None`, no bound
  is applied and `stale_observations_excluded_count` reads zero because nothing was
  checked — the engine records that in `audit_notes` rather than letting the zero
  read as reassurance.

## 4. Read-through screening

Suppliers whose `target_share_of_supplier_revenue_pct` is below
`min_read_through_share_pct` are **excluded**, not down-weighted, and counted in
`low_read_through_excluded_count`. A supplier's total revenue growth is a blend of
every customer it has; where the target is a small slice, including it at any weight
injects unrelated demand into the estimate.

The default floor of 10% is anchored by analogy to the ASC 280 materiality line. The
analogy is not a rule — calibrate it.

## 5. Concentration weighting and the blend

$$\bar g_{\text{sup}} = \frac{\sum_i w_i g_i}{\sum_i w_i}, \qquad
  \bar h_{\text{cust}} = \frac{\sum_j v_j h_j}{\sum_j v_j}$$

$$\text{Implied} = W_s \, \bar g_{\text{sup}} - W_c \, \bar h_{\text{cust}}$$

- Dividing by the observed weight total **extrapolates**: it assumes the unobserved
  part of the chain grew like the observed part. That is the only assumption
  available, which is why coverage is reported and gated rather than hidden.
- $W_s = 0.70$ and $W_c = 0.30$ are house defaults. The *direction* of $W_s < 1$ is
  supported by the bullwhip effect — Lee, Padmanabhan & Whang (1997) show order
  variance exceeds sales variance and amplifies moving upstream, so a supplier's
  swing overstates the target's end demand. The *magnitude* is not supported by any
  published source; re-estimate it by regressing realized target revenue growth on
  the two weighted terms.
- With no usable customer observations the drag term is `0.0` and an audit note
  records that the signal is supplier-only. That is an absence of measurement, not a
  measurement of zero channel inventory build.
- Duplicate tickers raise, and weights summing above 100% raise: a supplier set
  cannot exceed the target's whole input spend, and a duplicated link double-weights
  itself.

## 6. Standardization

$$\text{Gap} = \text{Implied} - \text{Consensus revenue growth}, \qquad
  Z = \frac{\text{Gap}}{\sigma_{\text{consensus}}}$$

- Both terms must be **revenue** growth. An EPS consensus in the second position
  produces a well-formed number with no interpretation, and the engine cannot detect
  it.
- $\sigma_{\text{consensus}}$ is supplied per call and validated finite and strictly
  positive at entry, so there is no division guard here and no path that returns a
  confident $Z = 0$ from a broken configuration.
- Use either the cross-analyst dispersion of the revenue estimates for this
  company-quarter or the historical standard deviation of this model's own realized
  gap. `surprise_z_threshold` must be calibrated against whichever you chose.

## 7. Gate, then band

- Below `min_supplier_coverage_pct`, or with no usable suppliers at all:
  `surprise_z_score`, `implied_revenue_growth_pct` and `consensus_revenue_gap_pct` are
  `None`, `directional_signal` is `INSUFFICIENT_DATA`, `is_signal_measurable` is
  `False`, and the suppression is logged at WARNING so it lands in the audit trail.
- `None` means "not measurable". Rendering it as `0.0` fabricates a measurement.
- Bands are decided on the **unrounded** $Z$, inclusive at each edge:

| Condition | `directional_signal` |
|---|---|
| $Z \ge$ `surprise_z_threshold` | `BUY_EARNINGS_SURPRISE` |
| $\|Z\| <$ `surprise_z_threshold` | `NEUTRAL` |
| $Z \le -$`surprise_z_threshold` | `SELL_EARNINGS_DISAPPOINTMENT` |

`BUY_EARNINGS_SURPRISE` is a directional bias on a fundamental estimate, not an
order instruction.

## 8. Audit output

`SupplyChainEarningsSignal` carries everything needed to reconstruct the decision:
`as_of_iso`, `asset_id`, `directional_signal`, `is_signal_measurable`,
`surprise_z_score`, `implied_revenue_growth_pct`, `consensus_revenue_gap_pct`, both
consensus inputs, both weighted aggregates, `supplier_coverage_pct` and
`customer_coverage_pct`, submitted-vs-used counts on both sides, the three exclusion
counters, `declared_lead_time_months`, and `audit_notes`.

Persist the record **with the configuration that produced it**. Every threshold is a
parameter, so the Z-score is not reproducible from the inputs alone.

## 9. Deprecated surface

- `Config`, `Engine` and `SupplierCustomerLink` are import-compatibility shims. The
  scoring path reads none of them; `SupplierObservation` supersedes
  `SupplierCustomerLink`.
