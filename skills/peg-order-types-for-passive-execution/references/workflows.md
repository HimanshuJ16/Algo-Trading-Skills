# Workflows for Peg Order Types for Passive Execution

Order of operations matters: the offset is applied to the reference, bounds are
applied to the offset price, and quantization is last. Quantizing before
clamping lets a rounded price sit a fraction of a tick through a bound.

## 1. Specification validation (raises `PegSpecError`)

Construction of `PegOrder`, `NBBOQuote` and `PegPricingConfig` validates:

- `side` and `peg_type` resolve to a known enum member — an unrecognised value is
  rejected, never defaulted. A silent fall-through prices a "buy" on the sell
  side with an inverted offset and an inverted cap.
- `offset` is finite; `quantity` and `limit_cap` are finite and positive.
- `is_short_sale` is set only on a SELL.
- `tick_size` and LULD bands are positive, and `luld_lower_band <= luld_upper_band`.
- `reprice_threshold_ticks >= 1`.

Float inputs are converted through `str`, so `0.07` becomes `Decimal("0.07")`
rather than the binary expansion.

## 2. Market-state classification (returns `SUSPENDED`, does not raise)

Checked in order; the first failure suspends:

| Check | Reason | Rationale |
|---|---|---|
| `order.symbol == nbbo.symbol` | `SYMBOL_MISMATCH` | Pegging to another instrument's quote is silent and catastrophic |
| Both prices numeric and not NaN | `NON_FINITE_QUOTE` | `nan <= 0` is `False`; NaN survives a naive validity check and yields a NaN limit price |
| Both prices `> 0` | `NON_POSITIVE_QUOTE` | A zeroed field is an absent quote, not a free order |
| `bid <= ask` | `CROSSED_MARKET` | A crossed consolidated quote indicates stale or bad data |

A locked book (`bid == ask`) is **not** a failure. It is legal, and a midpoint
peg there equals the locking price.

Suspension returns `effective_limit_price = None`. There is no fallback price;
callers must skip the tick rather than substitute a last-known-good value.

## 3. Reference price

| Peg type | BUY | SELL |
|---|---|---|
| `PRIMARY` | best bid | best ask |
| `MIDPOINT` | `(bid + ask) / 2` | `(bid + ask) / 2` |
| `MARKET` | best ask | best bid |

## 4. Offset

Side-relative and aggressive-positive:

```
raw = reference + offset   (BUY)
raw = reference - offset   (SELL)
```

A negative offset is passive. This is Nasdaq's Offset Amount convention, not the
FIX `PegOffsetValue(211)` convention — negate for SELL when emitting FIX.

## 5. Price increment

- Non-displayed Midpoint peg with `allow_subpenny_midpoint` → `tick / 2`. The
  midpoint of two tick-aligned quotes always lands exactly on the half-tick
  lattice, so this is exact rather than approximate.
- Everything else → `tick`.

## 6. Protective bounds

Collected for the order's side, then the tightest binds:

| Constraint | BUY (ceiling) | SELL (floor) | Applies when |
|---|---|---|---|
| `PASSIVITY` | `ask - increment` | `bid + increment` | `enforce_non_marketable` |
| `SHORT_SALE_201` | — | `bid + tick` | SELL, `is_short_sale`, `short_sale_restricted` |
| `LULD_BAND` | `luld_upper_band` | `luld_lower_band` | band supplied |
| `LIMIT_CAP` | `limit_cap` | `limit_cap` | cap supplied |

Every bound on a side points the same direction, so no two can conflict. Each
bound is snapped onto the lattice in the passive direction before it is applied,
which is what stops aggressive rounding from crossing it.

`clamps` lists every bound that cut either the raw peg price (the economic
intent was reduced) or the rounded price (the bound produced the submitted
number). `binding_constraint` names the bound that set the final price;
`CONSTRAINT_PRECEDENCE` breaks ties in favour of the regulatory rule, so an audit
trail attributes a clamp to the rule that compelled it rather than to a
coincident house limit.

If the bounds resolve to a non-positive price — a Market peg buy on a book whose
offer is one tick — the result is `SUSPENDED` / `UNPRICEABLE`, not a zero price.

## 7. Quantization

`PASSIVE` (FIX `PegRoundDirection=2`) floors a buy and ceils a sell.
`AGGRESSIVE` (`=1`) does the reverse and is re-clamped to the lattice-aligned
bound afterwards.

## 8. Repricing dispatch

`should_reprice(active_limit_price, report, threshold_ticks=None)` returns a
`RepriceDecision`:

| Condition | `should_reprice` | Reason |
|---|---|---|
| Report is `SUSPENDED` or has no price | `False` | `NO_VALID_PRICE` |
| No active order | `True` | `NO_ACTIVE_ORDER` |
| `abs(new - active) / tick >= threshold` | `True` | `THRESHOLD_MET` |
| Otherwise | `False` | `BELOW_THRESHOLD` |

The threshold exists because every cancel/replace costs a message from the
venue's order-to-trade budget and resets queue priority. Raise it in noisy names;
never remove it.

## 9. Audit

`PegOrderReport` carries the reference price, the raw offset price, the final
price, the increment used, the clamps, the binding constraint, whether the final
price is marketable, and a rendered `audit_notes` line. Persist the report, not
just the price — the price alone cannot answer why an order rested where it did.
