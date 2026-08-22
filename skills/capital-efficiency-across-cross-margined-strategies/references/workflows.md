# Workflow: Estimating Cross-Margin Capital Efficiency

## 1. Assemble one row per instrument

- Pull positions from every strategy sleeve as `(symbol, signed delta_usd,
  standalone base_margin_usd)`.
- **Net per symbol before margining.** Call `net_positions_by_symbol(positions)`. Two
  sleeves holding the same instrument are one net position to the broker; margining them
  as two legs and granting a spread credit between them overstates a partly flat book.
  `calculate_margin` raises `MarginInputError` on duplicate symbols rather than guessing.
- Rows must carry non-negative margin and finite delta. Anything else raises.

## 2. Choose credit rates, in this order

1. **Published offset percentage** for the pair — SPAN inter-commodity credit table, OCC
   product-group offset, or the broker's margin documentation. Pass via
   `credit_rate_overrides`; used as given, with no haircut on top.
2. **Correlation proxy**, used only where no published rate exists. Supply the matrix and
   a `correlation_haircut` reflecting how far you distrust it. Missing pairs get 0.0 —
   absence grants no credit.

Where the venue is known to grant nothing — US single-name equity class groups under OCC
CPM — pass an explicit `0.0` rather than letting a correlation invent a credit.

## 3. Run the estimate

```python
optimizer = CrossMarginOptimizer(
    correlation_matrix=corr,
    correlation_haircut=0.80,
    credit_rate_overrides={"CORN": {"SOYB": 0.65}},
    min_cross_margin_fraction=0.25,
)
report = optimizer.calculate_margin(net_positions_by_symbol(positions))
```

Spreads form highest-credit-first — mirroring SPAN's exchange-defined spread priority — so
the result is a function of the portfolio, not of the order rows arrived in. Each spread
consumes the smaller leg's remaining margin from both legs, so no leg is credited twice.

## 4. Read the report critically

- `offsets` is the audit trail: each entry names both legs, the rate, the amount spread
  and whether the rate was `published` or `correlation`. A total built mostly from
  `correlation` sources is a total to distrust.
- `floor_applied` means `min_cross_margin_fraction`, not the model, produced the answer.
- `capital_efficiency_ratio` is bounded above by 2.0 by construction. Interpret 1.5x as
  "the estimator thinks a third of the standalone requirement may be released", not as a
  guarantee.

## 5. Reconcile before allocating

- Compare the estimate against the broker's actual requirement on the live book, via its
  margin calculator or API. This is the only step that validates the credit rates.
- Persistent optimism means the rates are wrong. Fix the rates; raising the haircut to
  compensate hides the error in a parameter that has no meaning.
- Only then treat released collateral as available — and route it to **uncorrelated**
  exposure. Redeploying it into the same correlated risk removes the offset that produced
  it. Enforce the deployment decision through
  `multi-strategy-capital-allocation-limits` and `margin-utilization-circuit-breaker`
  rather than inside this estimator.
