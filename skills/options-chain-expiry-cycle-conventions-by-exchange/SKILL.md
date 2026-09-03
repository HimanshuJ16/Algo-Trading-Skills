---
name: options-chain-expiry-cycle-conventions-by-exchange
description: >-
  Use when a chain model, roll scheduler or expiry-day process needs the contractual
  terms rather than a rule of thumb: the monthly-cycle expiry date per exchange, the
  last trading day, and when they differ.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: options-conventions, expiry-cycles, cboe-spx, am-settlement, pm-settlement, 3rd-friday, dte, derivatives
  brokers_frameworks: "Cboe Options Exchange; CME Group; Eurex; Deribit; OCC; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when an options chain model, backtester, roll scheduler or
expiry-day risk process needs the *contractual* terms of a listed option rather
than a rule of thumb. It answers four questions that are routinely conflated:

| Question | Why the obvious answer is wrong |
|---|---|
| **When does it expire?** | "Third Friday" holds on Cboe, CME and Eurex. Deribit monthlies expire the **last** Friday, and Cboe VIX options expire on a **Wednesday** — 30 days before the third Friday of the *following* month. |
| **When does it stop trading?** | For AM-settled monthlies (SPX, NDX, RUT, VIX) trading ceases on the business day **preceding** the expiration date. Treating expiry day as tradeable overstates the position's life by a day. |
| **How does it settle?** | AM vs PM is a US-centric pair. Eurex settles from a **Xetra intraday auction**; Deribit from a **fixed 08:00 UTC** delivery price. |
| **Is it European or American, cash or physical?** | Not derivable from the ticker. XSP and NDXP are index options that are PM-settled; CME's quarterly ES options are **American** and deliver a **futures** position. |

The engine resolves these from a registry of sourced contract specifications,
returning a `OptionsChainConventionReport` with signed DTE, the last trading
date, provenance (`source`, `source_as_of`) and any unverified-input warnings.

## When NOT to Use

- **Not a listed-expiry calendar.** It derives *anchored* monthly-cycle expiries
  arithmetically. Weekly and end-of-month series (`SPXW`, `NDXP`, `RUTW`) are not
  determined by a `(year, month)` pair, so `resolve_conventions()` refuses them.
  Use `get_contract_convention()` for their terms and take the date from the
  exchange's published expiry calendar.
- **Not a holiday calendar.** It will not invent one. Supply
  `holiday_calendar=` to get the "preceding business day" roll-back; without it
  the date is returned unadjusted **and flagged** in `report.warnings`. See
  `global-exchange-holiday-calendar-handling`.
- **Not a reference-data service.** The bundled registry is a worked example of
  eleven contracts across four venues, each carrying `source` and
  `source_as_of`. Exchanges change contract specifications — re-verify before
  relying on an entry, or inject your own via `registry=`.
- **Not a pricing, margin or exercise engine.** It returns conventions, not
  Greeks, settlement prices or assignment decisions.
- **Not an intraday clock.** Everything is date-granular. Cash settles on the
  business day *following* expiration, and the AM/PM distinction is a settlement
  *basis*, not a timestamp this module computes.

## Prerequisites

- Exchange code (`CBOE`, `CME`, `EUREX`, `DERIBIT`) and underlying symbol.
- Reference date and target `(year, month)` for the expiry being resolved.
- For symbols outside the registry, an explicit `asset_class` (`EQUITY`/`ETF`) —
  the module never infers conventions from a ticker string.
- Optional but strongly recommended: the exchange's non-trading days, ideally as
  a `{exchange: [dates]}` mapping so one venue's calendar cannot be applied to
  another.
- Python 3.7+. Standard library only (`dataclasses`, `datetime`, `logging`).

## Workflow

1. **Resolve the contract before resolving anything else.** Look up
   `(exchange, symbol)` in the registry. If it is absent and no `asset_class`
   was declared, **stop** — do not fall back to a default. An unrecognised index
   symbol defaulted to American/physical is the failure mode this step exists to
   prevent.
2. **Reject a cycle the contract cannot express.** `WEEKLY` is not derivable
   from `(year, month)` for any contract. `QUARTERLY` is valid only in the
   venue's quarterly months. CME `ES` accepts `QUARTERLY` only, because its
   European-style Third-Friday Monthly series is a *different product* — resolving
   one under the other's symbol reports the wrong exercise style.
3. **Apply the venue's expiry rule, not the third-Friday default.**
   `THIRD_FRIDAY` for Cboe/CME/Eurex, `LAST_FRIDAY` for Deribit,
   `VIX_30_DAY_WEDNESDAY` for VIX. Compute it arithmetically — never via
   `calendar.monthcalendar()`, whose column layout depends on the process-global
   `setfirstweekday()`.
4. **Roll back off a non-trading day, using that exchange's own calendar.** Cboe
   and Eurex both specify the third Friday "or the immediately preceding business
   day if the Exchange is not open on that Friday". If no calendar covers the
   exchange, return the unadjusted date **with a warning** rather than guessing.
   Skip this entirely for continuously-traded venues: Deribit has no closures to
   roll off, so adjusting would introduce the error.
5. **Derive the last trading day from the settlement basis.** AM-settled ⟹ the
   preceding business day, because the settlement value is struck at the open of
   the expiration date and the contract is no longer tradeable. PM-settled,
   auction-settled and fixed-time ⟹ the expiration date itself.
6. **Report signed DTE.** Negative means already expired. Clamping at zero makes
   an expired contract indistinguishable from one expiring today.
7. **Carry provenance into the report** — `source`, `source_as_of` and every
   warning — so a downstream audit can tell a verified date from an unverified one.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming every monthly option expires on the third Friday.** Deribit
  monthlies are the last Friday — in April 2022 that was the 29th, two weeks
  after the Cboe third Friday on the 15th. Cboe VIX options expire on a
  Wednesday and never on a Friday at all.
- **Trading an AM-settled monthly on its expiration date.** SPX, NDX and RUT
  standard monthlies stop trading the business day before. The Friday-morning
  SOQ is struck from component *opening* prices and can gap far from Thursday's
  close, so a position held past Thursday cannot be exited at all.
- **Assuming the third Friday is always a trading day.** Good Friday fell on the
  third Friday in April 2022 and April 2025; expiration moved to Thursday the
  14th and Thursday the 17th respectively. For an AM-settled contract the last
  trading day then moves to the Wednesday.
- **Inferring exercise style or settlement from the ticker.** XSP is an index
  option that is PM-settled; NDXP and RUTW likewise. CME quarterly ES options
  are American and exercise into a futures position, not cash and not shares.
- **Applying one venue's holiday calendar to another.** A US calendar is not a
  Eurex calendar. Key holidays by exchange, and treat an uncovered exchange as
  unverified rather than borrowing.
- **Deriving the third Friday from `calendar.monthcalendar()`.** Its week layout
  follows the process-global `calendar.setfirstweekday()`; any library that
  changes it silently shifts the result to a different weekday.
- **Clamping DTE at zero.** An expired contract then looks like a 0-DTE
  contract, which in a backtest reads as a live position to be managed.

## Verification

- Instantiate `OptionsChainExpiryConventionsEngine`. Query `CBOE`/`SPX`,
  January 2024, `MONTHLY` ⟹ expiry `2024-01-19`, last trading day `2024-01-18`,
  `AM_SETTLED`, `EUROPEAN`, `CASH`. Query `DERIBIT`/`BTC`, March 2026 ⟹
  `2026-03-27` (last Friday), **not** `2026-03-20`. Query `CBOE`/`XSP` ⟹
  `PM_SETTLED` despite being an index option. Query an unregistered symbol with
  no `asset_class` ⟹ `UnknownContractError`, never a guessed default.
- With `holiday_calendar={"CBOE": ["2025-04-18"]}`, query `CBOE`/`SPX` April
  2025 ⟹ expiry `2025-04-17`, last trading day `2025-04-16`,
  `holiday_adjusted=True`.
- Run `python -m unittest discover -s skills/options-chain-expiry-cycle-conventions-by-exchange/scripts`.

## Related Skills

- `options-chain-data-normalization-across-vendors`
- `options-pin-risk-management-at-expiry`
- `american-vs-european-style-option-exercise-handling`
- `physical-vs-cash-settlement-handling`
- `global-exchange-holiday-calendar-handling`
