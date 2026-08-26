# Deep Workflow Reference — multi-currency-pnl-and-fx-conversion

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Rate direction convention

Fixed module-wide, and the first thing to get right:

```
get_rate(from_ccy, to_ccy, timestamp) -> units of `to_ccy` per one unit of `from_ccy`

get_rate("USD", "INR", t) -> 83.50    # 1 USD buys 83.50 INR
get_rate("EUR", "USD", t) ->  1.09    # 1 EUR buys 1.09 USD
```

This is deliberately **not** the interbank pair convention. `EUR/USD = 1.09` happens to
line up with `get_rate("EUR", "USD")`, but `USD/JPY = 155` lines up with
`get_rate("USD", "JPY")` — so a provider built by splitting pair strings onto
`(from_ccy, to_ccy)` is inverted for roughly half the majors. An inverted rate produces a
plausible number, never an exception. Normalise orientation upstream
(`currency-pair-quoting-convention-normalization`) and unit-test the provider against a
pair quoted each way round.

## Full procedure

### 1. Tag every amount

```python
from fx_convert import CurrencyAmount

position_pnl = CurrencyAmount(amount=1_250.00, currency="USD",
                              timestamp=datetime.datetime(2024, 3, 15, 20, 0))
```

Never store a bare float. Keep the native-currency record intact for reconciliation
against the broker's own native-currency statement — converting at entry and discarding
the native figure makes a later fee-versus-FX discrepancy unattributable.

### 2. Load a point-in-time rate series

`HistoricalFXRateStore` resolves as-of: the newest observation at or before the requested
timestamp, never a later one. It is directly usable as a provider.

```python
from fx_convert import HistoricalFXRateStore, PointInTimeFXResolver, MultiCurrencyPnLEngine

store = HistoricalFXRateStore(max_staleness=datetime.timedelta(days=3))
store.add_rate("USD", "INR", datetime.datetime(2024, 1, 1), 83.10)
store.add_rate("USD", "INR", datetime.datetime(2024, 3, 15), 83.45)

resolver = PointInTimeFXResolver(rate_provider_fn=store,
                                 pivot_currencies=("USD",),
                                 require_timestamp=True)   # set True in backtests
engine = MultiCurrencyPnLEngine(fx_resolver=resolver)
```

Four conditions are made loud rather than silent:

| Condition | Behaviour |
|---|---|
| Request predates the series | `FXRateUnavailableError` — borrowing the first known rate is lookahead |
| Newest rate older than `max_staleness` | `FXRateUnavailableError` — a halted or unquoted session must not value a position |
| `timestamp=None` against the store | `FXConversionError` — an implicit "latest" is the contamination the store exists to prevent |
| Naive vs tz-aware timestamps mixed | `FXConversionError` naming the mismatch, not a bare `TypeError` from deep inside a comparison |

`require_timestamp=True` extends the third guard to the whole resolver, so any
untimestamped lookup in a backtest path fails instead of quietly resolving.

### 3. Resolve, with triangulation

`PointInTimeFXResolver.get_rate` tries, in order:

1. **Direct** — `provider(from, to)`.
2. **Inverse** — `1 / provider(to, from)`.
3. **Pivot** — `provider(from, P) * provider(P, to)` for each `P` in `pivot_currencies`,
   each leg falling back to its own inverse. `USD` by default; configurable for books
   that cross through `EUR`.

A provider signals "cannot serve this pair" by raising `FXRateUnavailableError`, raising
`KeyError`, or returning `None`. Every leg is checked for finiteness and strict
positivity. **If no path exists, `get_rate` raises.** It has no built-in rate table and
never returns `1.0` as a fallback: parity is indistinguishable from a correct conversion
once it is in the output.

### 4. Convert and aggregate

```python
total_usd = engine.aggregate_in_base_currency([
    CurrencyAmount(1_250.00, "USD", t1),
    CurrencyAmount(84_500.00, "INR", t2),
    CurrencyAmount(310_000.00, "JPY", t3),
], base_currency="USD")
```

Each leg is converted at **its own** timestamp when it carries one, falling back to the
call-level `timestamp`. The sum is accumulated at full precision and quantised **once**.

Rounding each leg first accumulates error without bound in the leg count: with a
0-decimal base, 1,000 legs of ¥0.5 round to ¥0 each and report an exposure of zero. A leg
that cannot convert raises with its index — do not catch and skip it, because a dropped
leg understates the aggregate silently.

### 5. Decompose price P&L from FX P&L

With $q$ the signed quantity, $P_0, P_1$ the native entry/exit prices, and $X_0, X_1$ the
native-to-base rates at entry and exit:

$$\text{total} = q P_1 X_1 - q P_0 X_0$$

`calculate_decomposed_pnl` reports the **entry-rate price effect** convention:

| Field | Expression | Meaning |
|---|---|---|
| `native_pnl` | $q(P_1 - P_0)$ | P&L in the native currency, for broker reconciliation |
| `native_price_pnl` | $q(P_1 - P_0)X_0$ | price move valued at the entry rate |
| `fx_translation_pnl` | $q P_1 (X_1 - X_0)$ | FX move on the exit notional |
| `total_base_pnl` | sum of the two above | equals the direct valuation exactly |
| `fx_on_entry_notional` | $q P_0 (X_1 - X_0)$ | pure FX effect on the entry notional |
| `price_fx_interaction` | $q(P_1 - P_0)(X_1 - X_0)$ | the price × FX cross term |

Expanding `fx_translation_pnl` gives `fx_on_entry_notional + price_fx_interaction`, so
**the whole interaction term sits inside the FX leg** under this convention. Attribution
frameworks differ on where it belongs (see `references/standards.md`), which is why both
sub-terms are reported: a broker or attribution system that splits the cross term
differently will disagree with `fx_translation_pnl` while agreeing on `total_base_pnl`.

Rounding: `native_price_pnl` and `total_base_pnl` are each quantised from the raw value
and `fx_translation_pnl` is taken as the **difference**, so the two components always sum
to the reported total. Rounding all three independently breaks that identity — with a
0-decimal base, a raw split of 0.40 + 4.16 reports `0 + 4` against a total of `5`.
`entry_fx_rate` and `exit_fx_rate` are returned unrounded: they are rates, not money.

### 6. Precision

Fiat precision follows ISO 4217 minor units (`ISO_4217_MINOR_UNITS`); everything not in
the table falls back to 2 decimals **with a logged warning**, because a silent 2-decimal
default misstates KRW (0) and KWD (3). Crypto is not ISO 4217 — register the venue's own
increments:

```python
engine = MultiCurrencyPnLEngine(fx_resolver=resolver, minor_units={"USDT": 6})
engine.register_currency_precision("SOL", 4)
```

Registration is per-engine and does not mutate the module-level table.

Monetary rounding is **half-up** on the decimal value, not `round()`, which is
half-to-even on a binary float: `round(2.675, 2)` is `2.67`, while
`engine.round_amount(2.675, "USD")` is `2.68`.

## Failure modes observed in production

- **Silent parity conversion.** A rate provider that returns `1.0` for any pair it does
  not carry, converting BTC to USD at par. No output check catches it.
- **Undated reference rates as a default.** Hard-coded constants that ignore the
  timestamp argument entirely, turning a point-in-time API into a static one without
  changing a call site.
- **Inverted quote orientation** for half the majors, from mapping pair strings onto
  `(from, to)` without normalising base/terms.
- **Unconverted summation** of USD and INR P&L into a meaningless total.
- **Entry-time conversion overwrites** that discard the native figure and destroy broker
  reconciliation permanently.
- **Current-rate historical backtesting** — today's spot applied across five years.
- **Per-leg rounding drift**, up to and including a zeroed aggregate in a 0-decimal base.
- **Components that do not sum to their own total** after independent rounding.
- **Conflated FX gains**, blending strategy performance with passive currency movement.
- **Official-but-unrealisable rates** for currencies under capital controls; see the
  IAS 21 *Lack of Exchangeability* amendment in `references/standards.md`.

## Production implementation reference

- Reference code: `scripts/fx_convert.py` — `CurrencyAmount`, `DecomposedPnL`,
  `HistoricalFXRateStore`, `PointInTimeFXResolver`, `MultiCurrencyPnLEngine`,
  `FXConversionError`, `FXRateUnavailableError`.
- Automated unit tests: `scripts/test_fx_convert.py`.

### Migration from 1.x

- `PointInTimeFXResolver()` no longer ships rates. A bare resolver raises
  `FXRateUnavailableError` on every non-identity pair; inject `rate_provider_fn`.
- The `1.0` fallback for unknown pairs is gone. Callers that relied on it were converting
  at parity.
- `MultiCurrencyPnLEngine.convert` returns a new `CurrencyAmount` on the same-currency
  path, upper-cased and rounded, instead of the caller's own object.
- `round_amount` is half-up rather than half-to-even, and `KRW` now correctly rounds to
  0 decimals.
- `DecomposedPnL` gains `native_pnl`, `fx_on_entry_notional` and `price_fx_interaction`,
  appended with defaults so existing positional construction still works.
- Module-level `convert` / `aggregate_in_base_currency` are unchanged and remain
  available; new code should use the engine.
