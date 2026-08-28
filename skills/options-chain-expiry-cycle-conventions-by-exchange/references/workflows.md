# Workflows for Options Chain Expiry Cycle Conventions by Exchange

Deep procedure behind `SKILL.md`. Every step names the decision point and the
failure it prevents.

## 1. Resolve the contract before resolving any date

```python
convention = engine.get_contract_convention(exchange, symbol, asset_class=None)
```

Look up `(EXCHANGE, SYMBOL)` in `CONTRACT_REGISTRY`, case- and
whitespace-normalised.

- **Registered** ⟹ use it. A registered contract always wins over a declared
  `asset_class`, so passing `asset_class="EQUITY"` alongside `SPX` cannot
  downgrade it to an American physically-settled option.
- **Not registered, `asset_class` declared** ⟹ use the class default. Declaring
  the class is an explicit statement by the caller.
- **Not registered, nothing declared** ⟹ raise `UnknownContractError`. **Stop.**

Do not add a "best effort" fallback here. The concrete counterexamples are all
live products: `XSP` and `NDXP` are index options that are PM-settled, `RUTW`
likewise, and `VIX` does not expire on a Friday. A default of
American/physical/PM for anything unrecognised is wrong for every one of them,
and wrong silently.

## 2. Reject a cycle the contract cannot express

```python
engine._check_cycle(convention, cycle, month)   # via resolve_conventions()
```

- **`WEEKLY`** is never derivable from `(year, month)` — a month holds four or
  five of them. `SPXW`, `NDXP` and `RUTW` carry
  `expiry_rule=RULE_NOT_CALENDAR_DERIVABLE` and an empty `supported_cycles`, so
  `resolve_conventions()` raises `UnsupportedCycleError`. Their settlement and
  exercise terms are still available from `get_contract_convention()`; take the
  date from the exchange's published expiry calendar.
- **`QUARTERLY`** is valid only in the venue's quarterly months
  (`3, 6, 9, 12`). A quarterly query for January is a caller bug, not a date to
  be computed.
- **CME `ES` accepts `QUARTERLY` only.** CME lists a separate European-style
  Third-Friday Monthly series on the same underlying. Resolving a monthly under
  the quarterly symbol would report `AMERICAN` for a European contract — an
  early-assignment risk that does not exist.
- **`LEAPS`** uses the same third-Friday anchor in a future year.

## 3. Apply the venue's expiry rule

| Rule | Derivation |
|---|---|
| `THIRD_FRIDAY` | First Friday of the month `+ 14 days`. |
| `LAST_FRIDAY` | Last day of the month, stepped back to the nearest Friday. |
| `VIX_30_DAY_WEDNESDAY` | Third Friday of the **following** calendar month `- 30 days`. |

Compute all three arithmetically from `date.weekday()`.

> **Never use `calendar.monthcalendar()`.** It returns week rows laid out
> according to the process-global `calendar.setfirstweekday()`. Reading
> `week[calendar.FRIDAY]` is only correct while that global is at its default;
> any library that changes it makes the function return a different weekday,
> silently and process-wide. This is a real defect that the module's regression
> test pins.

The VIX rule is self-checking: 30 days before a Friday is always a Wednesday, so
the implementation asserts the resulting weekday and raises if the arithmetic
ever disagrees.

## 4. Roll back off a non-trading day — with the right exchange's calendar

Cboe and Eurex both specify the third Friday *"or the immediately preceding
business day if the Exchange is not open on that Friday."*

```python
engine = OptionsChainExpiryConventionsEngine(
    holiday_calendar={"CBOE": us_holidays, "EUREX": eurex_holidays}
)
```

Three cases, in order:

1. **`observes_exchange_holidays=False`** (Deribit) ⟹ no adjustment, no warning.
   The venue trades continuously; rolling an expiry off a US or European market
   holiday would *introduce* the error rather than correct it.
2. **A calendar covers this exchange** ⟹ step back to the preceding business
   day; set `holiday_adjusted=True`.
3. **No calendar covers this exchange** ⟹ return the unadjusted date and append
   an explicit `report.warnings` entry naming the exchange and date.

Prefer the `{exchange: days}` mapping form. The flat-iterable form applies to
whichever exchange is queried, which is safe only for single-venue use — a US
calendar applied to a Eurex query is a silent correctness bug, and an exchange
absent from the mapping is treated as uncovered rather than borrowing another
venue's closures.

Verified cases where this matters:

| Month | Third Friday | Why | Actual expiry |
|---|---|---|---|
| April 2022 | 2022-04-15 | Good Friday | 2022-04-14 (Thu) |
| April 2025 | 2025-04-18 | Good Friday | 2025-04-17 (Thu) |

## 5. Derive the last trading day from the settlement basis

| `last_trading_day_rule` | Applies to | Result |
|---|---|---|
| `PRECEDING_BUSINESS_DAY` | `AM_SETTLED` monthlies — `SPX`, `NDX`, `RUT`, `VIX` | Business day before the expiration date |
| `EXPIRATION_DATE` | `PM_SETTLED`, `AUCTION_SETTLED`, `FIXED_TIME_SETTLED` | The expiration date itself |

Cboe: *"Trading in SPX options will ordinarily cease on the business day (usually
a Thursday) preceding the day on which the exercise-settlement value (i.e., the
expiration date) is calculated."* The settlement value is struck from component
**opening** prices on expiration Friday morning, so the position cannot be
traded that day at all. A Friday-morning SOQ can gap well away from Thursday's
close, and there is no session in which to exit.

This compounds with step 4: when Good Friday moved April 2025 expiry to Thursday
the 17th, the last trading day for `SPX` moved to **Wednesday the 16th**.

## 6. Report signed DTE

```python
dte_days              = (expiry - reference_date).days              # signed
dte_to_last_trading_day = (last_trading_date - reference_date).days  # signed
is_expired            = expiry < reference_date
```

Never clamp at zero. A clamped negative makes an already-expired contract
indistinguishable from a 0-DTE contract, which a backtest or risk sweep reads as
a live position still to be managed.

Note that `dte_to_last_trading_day` is `-1` for an AM-settled contract queried on
its own expiration date: correct, and the signal that the position can no longer
be closed.

## 7. Carry provenance into the report

Every `ContractConvention` holds `source` and `source_as_of`; both are copied
onto the report along with `settlement_basis` and the full `warnings` tuple. A
downstream audit can then distinguish a holiday-verified expiry date from an
unverified one without re-deriving anything.

Warnings are returned structurally on `report.warnings` — that is the
programmatic contract. The module attaches a `logging.NullHandler`, so it emits
nothing to stderr unless the host application configures logging.

## 8. Maintaining the registry

The bundled registry is a worked example, not a reference-data service. When
extending it:

- Cite a primary source (exchange or clearing house) in `source`, and set
  `source_as_of` to the month you verified it.
- Set `supported_cycles=()` together with `RULE_NOT_CALENDAR_DERIVABLE` for any
  weekly or EOM series — the registry-integrity test enforces that these two
  agree.
- Set `observes_exchange_holidays=False` only for genuinely continuous venues.
- Inject wholesale via `registry=` rather than mutating the module-level dict, so
  the bundled data stays a readable reference.
