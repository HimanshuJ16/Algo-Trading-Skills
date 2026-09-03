# Standards for Adjusted and Unadjusted Price Series

## Series Semantics

| Series mode | Split treatment | Dividend treatment | Appropriate primary use |
|---|---|---|---|
| `UNADJUSTED` | Raw ex-date split jump remains | Cash dividend remains a separate portfolio event | Price-level and execution-aware research with explicit corporate actions |
| `SPLIT_ADJUSTED` | Historical prices/volumes normalized for splits | Cash dividend remains explicit unless vendor contract says otherwise | Technical signals and liquidity comparisons across split events |
| `TOTAL_RETURN_ADJUSTED` | Split effect embedded | Dividend reinvestment effect embedded | Total-return analysis when factor provenance and as-of availability are controlled |
| `UNKNOWN` | Do not infer | Do not infer | Audit-only mode pending data-contract confirmation |

A smooth series is not proof of correct adjustment. A cash-dividend ex-date drop in an unadjusted price series is not automatically a bias; it represents a cash distribution that must be credited separately in the portfolio ledger. Look-ahead risk depends on using adjustment factors or corporate-action knowledge before they were available to the historical decision process.

## Ratio Convention

The auditor uses one explicit convention:

- `SPLIT ratio = post-split shares per pre-split share`.
- A `2.0` ratio is a 2-for-1 split: backward-adjust historical prices by dividing by `2.0` and volumes by multiplying by `2.0`.
- A `0.5` ratio is a 1-for-2 reverse split: backward-adjust historical prices by dividing by `0.5` and volumes by multiplying by `0.5`.
- `DIVIDEND ratio = cash amount per share`, not a multiplicative price factor.

This matches the CRSP convention once the field mapping is applied. CRSP stores
`FACPR` as the number of *additional* shares per old share and forms the cumulative
factor from "the Factor to Adjust Prices variable + 1", so `split_ratio = FACPR + 1`:
a 2-for-1 split is `FACPR = 1.0`, and a reverse split has `FACPR` between -1 and 0,
giving a ratio between 0 and 1. CRSP adjusts price and dividend data as
`A(t) = P(t) / C(t)` and share and volume data as `A(t) = P(t) * C(t)`, which is the
divide-price / multiply-volume direction used here.

Note that CRSP's adjusted price series is **not** a total-return series: for ordinary
cash dividends `FACPR` is set to zero, so cash distributions do not move the adjusted
price at all and instead enter through the return series. A vendor field labelled
"adjusted close" may follow either convention; confirm which before declaring a mode.

## Composite Ex-Date Factors

When several actions share one ex-date, the individual factors multiply, and multiple
ordinary cash dividends on the same ex-date are summed into a single cash factor. For a
prior close `P`, splits `s_i` and cash amounts `d_j` sharing an ex-date:

```text
expected_price_ratio = product(1 / s_i) * (P - sum(d_j)) / P
```

The cash factor is referenced to the closing price on the day *preceding* the ex-date.
This formulation assumes each cash amount is quoted on the same pre-split share basis as
that prior close. When a vendor quotes a same-ex-date dividend on the post-split basis
instead, the two conventions disagree by exactly the split ratio — reconcile the basis
with the vendor before trusting a same-date split-plus-dividend match, rather than
inferring it from the price series.

A cash distribution does not change shares outstanding, so volume is rescaled only for
split-type events, never for cash dividends.

## Audit Invariants

- Dates are ISO-formatted and strictly increasing.
- Closes and opens are finite and positive; volumes are finite and non-negative.
- Price, volume, and date arrays have identical lengths; supplied opens align with closes.
- A rejected input cannot produce a partial report or transformation.
- A discontinuity is explained only when the observed close-to-open price ratio matches the composite expected ratio for every action on that ex-date, within `price_match_tolerance_pct`.
- The price-match tolerance and the volume tolerance are separate settings. The ex-date price factor is mechanical and warrants a tight tolerance; traded volume around an ex-date is not, and warrants a looser one. Reusing one loose tolerance for both converts genuine data errors into "explained" events.
- Split volume consistency compares observed volume ratio with `split_ratio` using a relative tolerance, not a fixed absolute difference.
- A cash amount that would imply a non-positive price yields no expectation; the jump is reported unexplained rather than matched.
- Every report records `boundary_source` as either `NEXT_OPEN` or `PRIOR_CLOSE_FALLBACK`.
- Series and corporate-action dates are canonicalized to `YYYY-MM-DD` before matching, so an alternate ISO spelling cannot silently orphan an action.
- Split transformations do not round every bar; downstream reconciliation applies an explicit numeric tolerance.
- No discontinuity means continuity only. Provenance, factor correctness, and point-in-time availability require external evidence.
- A matched split jump proves the series is neither split-adjusted nor total-return adjusted. A matched cash-dividend jump proves only that it is not total-return adjusted; it cannot separate raw from split-adjusted, and is reported as `NOT_TOTAL_RETURN_ADJUSTED`.

## Corporate-Action and Return Controls

- Preserve all same-date actions; a split and dividend may coexist.
- Compare price jumps against the actual next open when available. A close-only fallback must be recorded because it can misstate overnight discontinuities.
- Keep raw price, split-adjusted price, total-return factor, cash dividend, and share-count ledgers separate until the backtest return convention is selected.
- Do not mix declared series modes across a cross-asset universe without a documented conversion and reconciliation step.
- Retain the vendor identifier, factor version, action effective/ex-date, publication/as-of timestamp, and transformation parameters for replay.

## Reference Sources

- CRSP, *Data Description Guide*, Chapter 4 "Data Definitions", Factor to Adjust Price
  (p. 61): FACPR for splits is "the number of additional shares per old share issued";
  "For ordinary cash dividends or partial liquidating payments, Factor to Adjust Price is
  set to zero"; "In a reverse split, Factor To Adjust Price will be between -1 and 0."
  <https://leiq.bus.umich.edu/docs/crsp_factor_adjustment.pdf>
- CRSP, *Data Description Guide*, Chapter 5 "CRSP Calculations", Adjusted Data (p. 117):
  "Price and dividend data are adjusted with the calculation: A(t)=P(t)/C(t)"; "Share and
  volume data are adjusted with the calculation: A(t)=P(t)*C(t)"; "Where factor is
  typically the Factor to Adjust Prices variable + 1."
  <https://leiq.bus.umich.edu/docs/crsp_calculations_splits.pdf>
- Xignite (QUODD), *Corporate Actions Handling in GlobalHistorical v3*, Adjustment
  Principles: "Dividend adjustment factor = (Previous day closing price - Dividend
  amount) / (Previous day closing price)"; "If there are multiple corporate actions on
  the same EX date, individual adjustment factors are multiplied to compute the
  cumulative adjustment factor"; "If there are multiple ordinary cash dividends on the
  same EX date ... we sum up the multiple cash dividends to compute a single adjustment
  factor"; volume "is only adjusted for the corporate events that change the shares
  outstanding". <https://quodd.com/hubfs/corporate-actions-handling-in-globalhistorical-v3.pdf>
- FINRA Rule 5330, *Adjustment of Orders* (US broker-dealers): on the ex-date open order
  prices "shall be first reduced by the dollar amount of the dividend"; share quantities
  are adjusted by the split ratio unless marked "Do Not Increase"; open orders must be
  cancelled on a reverse split. Confirms that the prior close is the reference the market
  itself adjusts from on the ex-date.
  <https://www.finra.org/rules-guidance/rulebooks/finra-rules/5330>

Jurisdiction note: FINRA Rule 5330 binds FINRA member firms in the United States. CRSP
and vendor conventions are data-methodology standards, not regulatory requirements, and
other vendors and jurisdictions may differ — confirm against the contract in force.

## Scope Boundary

This auditor checks numeric continuity and declared corporate-action semantics. It does not establish vendor correctness, reconstruct missing corporate actions, determine tax treatment, or prove point-in-time availability. Those require vendor-specific records and a separate data-lineage control.