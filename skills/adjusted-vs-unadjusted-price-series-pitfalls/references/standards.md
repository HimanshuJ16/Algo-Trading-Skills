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

## Audit Invariants

- Dates are ISO-formatted and strictly increasing.
- Closes and opens are finite and positive; volumes are finite and non-negative.
- Price, volume, and date arrays have identical lengths; supplied opens align with closes.
- A rejected input cannot produce a partial report or transformation.
- A known split is explained only when the observed close-to-open price ratio matches `1 / split_ratio` within tolerance.
- Split volume consistency compares observed volume ratio with `split_ratio` using a relative tolerance, not a fixed absolute difference.
- Split transformations do not round every bar; downstream reconciliation applies an explicit numeric tolerance.
- No discontinuity means continuity only. Provenance, factor correctness, and point-in-time availability require external evidence.

## Corporate-Action and Return Controls

- Preserve all same-date actions; a split and dividend may coexist.
- Compare price jumps against the actual next open when available. A close-only fallback must be recorded because it can misstate overnight discontinuities.
- Keep raw price, split-adjusted price, total-return factor, cash dividend, and share-count ledgers separate until the backtest return convention is selected.
- Do not mix declared series modes across a cross-asset universe without a documented conversion and reconciliation step.
- Retain the vendor identifier, factor version, action effective/ex-date, publication/as-of timestamp, and transformation parameters for replay.

## Scope Boundary

This auditor checks numeric continuity and declared corporate-action semantics. It does not establish vendor correctness, reconstruct missing corporate actions, determine tax treatment, or prove point-in-time availability. Those require vendor-specific records and a separate data-lineage control.