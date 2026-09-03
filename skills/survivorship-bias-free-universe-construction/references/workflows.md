# Deep Workflow Reference — survivorship-bias-free-universe-construction

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

### 1. Reconcile the delisting-date convention

`InstrumentMetadata.delisting_date` is the **last date the security traded**, and
membership is inclusive at both ends:

```
listing_date <= as_of_date <= delisting_date
```

Before ingesting a vendor feed, establish which date it stores and convert. See the
conversion table in `references/standards.md`. The two conventions that get confused
in practice:

| Convention | Semantics | Example |
|---|---|---|
| Last trading date (this engine, CRSP `DLSTDT`) | Inclusive — the name traded on that date | TWTR: 2022-10-27 |
| Index deletion effective date | Half-open — effective prior to the open, the name is already out | An S&P deletion effective 2022-10-28 |

Loading the second as the first keeps every deleted name one session too long. On a
daily rebalance across a 500-name universe with 5% annual turnover, that is roughly 25
spurious position-days per year, each one on a name that was leaving for a reason.

### 2. Populate the security master, keyed by security

```python
engine.add_instrument(InstrumentMetadata(
    symbol="GM",
    name="General Motors Corporation",
    listing_date=date(1916, 9, 16),
    delisting_date=date(2009, 6, 1),
    delisting_reason=DelistingReason.BANKRUPTCY,
    delisting_return=SHUMWAY_1997_NYSE_AMEX_DELISTING_RETURN,
    security_id="PERMNO-12079",
))
engine.add_instrument(InstrumentMetadata(
    symbol="GM",
    name="General Motors Company",
    listing_date=date(2010, 11, 18),
    security_id="PERMNO-12369",
))
```

`security_id` defaults to the symbol. That default is safe only when no ticker in the
universe is recycled — which is not true of any multi-decade US equity universe.
Registering a duplicate `security_id` raises; it is a duplicate load or an id
collision, and neither should resolve by deleting a record.

Registration validates and rejects: a delisting date before the listing date, a
delisting reason with no date, a date with reason `ACTIVE`, an `ACTIVE` security
carrying terminal settlement data, a delisted security with neither or both terminal
value modes, a negative settlement price, a `delisting_return` below −1.0, a non-finite
number, and a `datetime.datetime` where a `date` is required.

### 3. Query the point-in-time universe per bar

| Call | Returns |
|---|---|
| `get_active_universe(T)` | Sorted unique symbols tradable on `T` |
| `get_active_securities(T)` | `InstrumentMetadata` records, sorted by `security_id` |
| `resolve_symbol(sym, T)` | The one security trading under `sym` on `T`, or `None` |

`get_active_universe` raises on a ticker collision — two live securities claiming one
symbol on one date. That is a listing-window defect in the metadata; silently picking
one puts the wrong company's returns in the backtest.

### 4. Settle the terminal value at `delisting_date`

Exactly one mode per security.

**Known terminal cash** — merger consideration, liquidation distribution, tender price:

```python
cash, msg = engine.process_delisting_settlement("CUSIP-90184L102", position_qty=100)
# 100 x $54.20 = $5,420.00
```

**Unknown terminal value, imputed from a delisting return:**

```python
cash, msg = engine.process_delisting_settlement(
    "PERMNO-84129", position_qty=100, last_traded_price=3.65
)
# 100 x $3.65 x (1 - 0.30) = $255.50
```

Rules the engine enforces:

- An `ACTIVE` security raises. There is no terminal value; mark it at market.
- `delisting_return` mode with no `last_traded_price` raises rather than defaulting to
  zero — zero is the loss the argument exists to measure.
- `position_qty` may be negative; a short in a name that went to zero pays off.
- `last_traded_price` must be strictly positive and finite.
- A recycled ticker must be settled by an identifier that names exactly one security.
  Ambiguity is refused across **both** namespaces: because `security_id` defaults to
  the symbol, an id can collide with a ticker another security also used. Register the
  old GM without an explicit id and its id is `GM` — the same string the new GM trades
  under. Resolving ids before tickers would silently pick one issuer, and which one
  would depend on load order. Supply explicit ids and the ambiguity does not arise.

Choosing the imputation:

| Venue | Constant | Source |
|---|---|---|
| NYSE / AMEX | `SHUMWAY_1997_NYSE_AMEX_DELISTING_RETURN` (−0.30) | Shumway (1997) |
| Nasdaq | `SHUMWAY_WARTHER_1999_NASDAQ_DELISTING_RETURN` (−0.55) | Shumway and Warther (1999) |

Use these **only where the delisting return is genuinely missing and the delisting is
performance-related**. Where the vendor reports an actual `DLRET`, pass that value —
an imputation over observed data is a fabrication.

### 5. Audit coverage over the backtest window

```python
audit = engine.audit_survivorship_bias(
    start_date, end_date,
    current_static_universe=todays_index_members,   # optional, enables the ghost check
    min_expected_attrition_rate=0.20,               # optional, your threshold
)
```

| Key | Meaning |
|---|---|
| `universe_in_period` | Securities tradable at some point inside the window — the denominator |
| `never_live_in_period` | Registered but never tradable in the window; a large value means the registry spans a wider era than the backtest |
| `delisted_in_period` / `delisted_symbols` | Names whose last trading date falls inside the window |
| `survivors_at_end` | Still tradable on `end_date` |
| `attrition_rate` | `delisted_in_period / universe_in_period`, `0.0` when the universe is empty |
| `ghost_count` / `ghost_symbols` | Tradable-in-window names absent from today's snapshot. **`None` means not audited** |
| `meets_expected_attrition` | `None` unless a threshold was supplied |

No key in this result asserts the universe is bias-free. The threshold is the caller's
because attrition depends on index, era and asset class, and must be recorded with the
audit or the result is not reproducible.

## Known Failure Modes

- **Ticker-keyed registry.** Registering the new General Motors Company discarded the
  old General Motors Corporation, and every pre-2010 universe query returned nothing
  where the old issuer belonged. The tool meant to remove survivorship bias created it.
- **Zero-defaulted settlement price.** A merger whose consideration was never populated
  settled at $0 — a total loss on a profitable cash exit, indistinguishable in the P&L
  from a real bankruptcy.
- **Settling an active position.** Calling settlement on a healthy name returned
  `qty × 0.0` with the message "Active instrument.", writing off the position silently.
- **Registry-wide audit denominator.** A universe of 100 names of which 99 had not yet
  listed reported a 1% delisting ratio and a `True` bias-protection flag over a window
  in which nothing traded at all.
- **Deleting instead of settling.** The name is present in the universe and the
  position simply disappears when the ticker stops printing. The loss is gone and the
  audit passes.
- **Imputing over an observed delisting return.** Applying −30% where CRSP reports an
  actual `DLRET` replaces data with an estimate, in the direction of the researcher's
  prior.

## Production Implementation Reference

- Reference code: `scripts/universe_builder.py` — `SurvivorshipFreeUniverseEngine`,
  `InstrumentMetadata`, `DelistingReason`, `UniverseError`, and the two imputation
  constants.
- Automated unit tests: `scripts/test_universe_builder.py` (51 tests). Run with
  `python -m unittest discover -s skills/survivorship-bias-free-universe-construction/scripts`.
