---
name: survivorship-bias-free-universe-construction
description: Use when building backtesting datasets to reconstruct point-in-time
  tradable universes that keep delisted, bankrupt and acquired instruments in history,
  and to settle a position's terminal cash value when a name stops trading — keyed by
  security rather than ticker so recycled symbols do not collapse two issuers into one.
  Resolves the listing/delisting axis and terminal settlement; it does not model index
  membership, announcement timing, or corporate-action price adjustment.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- survivorship-bias
- point-in-time
- delisted-stocks
- delisting-returns
- ticker-recycling
- universe-selection
brokers_frameworks:
- CRSP
- Sharadar
- Norgate Data
- QuantConnect Data
- Python standard library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a backtest needs the set of instruments that were actually tradable
on a historical date, and needs to settle positions in the ones that stopped trading.
Evaluating a strategy against only today's constituents drops every company that went
bankrupt, was acquired, or was dropped from the exchange — Enron, Lehman Brothers,
Wirecard, the old General Motors — and the strategy is credited with never having held
them.

Two failure modes, and the second is the expensive one:

| Failure | What it looks like | What this skill does |
|---|---|---|
| The name is missing from the universe | Today's S&P 500 applied to 2008 | `get_active_universe(T)` returns names by their listing window, delisted ones included |
| The name is in the universe but the loss is not | Position silently vanishes when the ticker stops printing | `process_delisting_settlement` books a terminal cash value and refuses to invent one |

**The magnitude is strategy-specific, not a constant.** Brown, Goetzmann, Ibbotson and
Ross (1992) concluded that survivorship bias definitely affects results but that its
size "is an empirical question"; published estimates for equity performance studies run
roughly 20–80 bp/year (Brown and Goetzmann, 1995) and 71–91 bp on three-factor alphas
(Elton, Gruber and Blake, 1996). Measure it on your own universe with the ghost audit
below rather than quoting a headline number.

## When NOT to Use

- **Not an index-membership model.** Listing and delisting bound *tradability*, not
  index membership — a name can be listed and out of the index. The
  addition/deletion event log and its half-open `[add_date, del_date)` interval belong
  to `point-in-time-index-constituent-tracking`.
- **Not an announcement-timing auditor.** This resolves *when a security traded*, not
  *when its fate became knowable*. A rebalance that must not act on an unannounced
  change needs the knowledge axis — use `backtest-look-ahead-in-universe-selection`.
- **Not a corporate-action price adjuster.** Splits and dividends are
  `corporate-action-adjusted-backtesting`. `last_traded_price` must arrive on the same
  basis as the position quantity.
- **Not a source of point-in-time data.** A clean audit means the metadata you supplied
  is internally consistent. Metadata reverse-engineered from a current-membership table
  produces a survivorship-biased universe that audits clean.
- **Not for live universe construction.** As-of machinery reconstructs the past; a live
  rebalance reads current listings directly.
- **Not books-and-records arithmetic.** Cash values are IEEE-754 floats.

## Prerequisites

- A listing record per security: `symbol`, `listing_date`, `delisting_date`,
  `delisting_reason`, and a terminal value.
- A **stable `security_id`** — CRSP PERMNO, CUSIP, SEDOL, FIGI. It defaults to the
  symbol, which is safe only for a universe in which no ticker is ever recycled.
- Knowledge of **which delisting date your vendor stores**. This engine wants the
  **last date the security traded** (CRSP `DLSTDT` convention), inclusive.
- For each delisted name, exactly one of: a known terminal cash price per share, or a
  delisting return to apply to the last traded price.
- Optionally, today's membership list, to run the ghost audit.

## Workflow

1. **Reconcile the date convention before ingesting anything.**
   Membership here is inclusive at both ends: `listing_date <= T <= delisting_date`,
   because `delisting_date` is the last day the security traded.
   **Decision point — an index deletion date is not a last trading date.** S&P DJI
   makes changes effective *prior to the open* on the effective date, so an index
   deletion date is half-open and the name is already gone that morning. Subtract one
   trading session before loading it here, or every deleted name lingers a session too
   long. Twitter alone has four defensible "delisting dates": merger effective
   2022-10-27, last NYSE trade 2022-10-27 (close $53.70), trading suspended before the
   open 2022-10-28, Form 25-NSE effective 2022-11-08. Only the second belongs in this
   field.

2. **Register by security, never by ticker.**
   Populate `security_id`. The old General Motors Corporation traded as `GM` until its
   2009 bankruptcy (moving to `GMGMQ`, then `MTLQQ` effective 2009-07-15); the new
   General Motors Company took `GM` at its 2010-11-18 IPO.
   **Decision point — a duplicate `security_id` raises rather than overwriting.** A
   ticker-keyed registry silently discards one of every recycled pair, which deletes
   the failed issuer from history — the exact bias this skill exists to remove,
   reintroduced by the tool meant to remove it.

3. **Query the point-in-time universe.**
   Call `get_active_universe(T)` per bar, or `get_active_securities(T)` to keep ids.
   Use `resolve_symbol(sym, T)` to answer "which company was `GM` in 2008".
   **Decision point — a ticker held by two live securities on one date raises.**
   Picking one silently is how a backtest ends up holding the wrong company's returns.

4. **Settle the terminal value when a position reaches `delisting_date`.**
   Two modes, exactly one per security:
   - `delisting_settlement_price` — known cash per share. Merger consideration
     ($54.20 for TWTR, which is **not** the $53.70 last close), liquidation
     distribution, tender price.
   - `delisting_return` — a fraction applied to `last_traded_price`, for when the
     terminal value is unknown.

   **Decision point — a delisted security with no terminal value fails registration.**
   The previous default of `0.0` settled a forgotten merger consideration at nothing:
   a silent, total, fabricated loss on a name that paid cash.

   **Decision point — bankruptcy is not automatically zero.** Lehman's common left the
   NYSE in September 2008 and went on trading over the counter as `LEHMQ` at non-zero
   prices for years; the position was sellable even though holders were eventually
   wiped out. When the delisting return is genuinely missing, impute it: **−30%** for
   NYSE/AMEX (Shumway 1997) or **−55%** for Nasdaq (Shumway and Warther 1999),
   available as module constants. Never let an imputation overwrite an observed
   delisting return.

   **Decision point — settling an ACTIVE security raises.** The old behaviour returned
   `qty × 0.0` with the message "Active instrument.", booking a silent 100% loss on a
   healthy position.

5. **Audit coverage over the backtest window.**
   `audit_survivorship_bias(start, end, current_static_universe=..., min_expected_attrition_rate=...)`.
   **Decision point — the denominator is names live *in the window*, not everything
   registered.** Counting names that never traded in the window inflates the
   denominator with securities the backtest could not have held and deflates the
   attrition rate the audit exists to report.
   **Decision point — `ghost_count is None` means *not audited*, never zero.** Pass
   today's membership to count names your history has and today's index does not; a
   snapshot-built universe scores zero by construction. Read it as a lower bound —
   the check compares tickers, so a recycled ticker hides its delisted issuer.

> Full procedure: see `references/workflows.md`.
> Standards and sourcing: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying a current snapshot backwards**: querying today's S&P 500 and running it
  across ten years of history. The ghost audit returns zero for exactly this universe,
  which is the tell.
- **Keying the registry by ticker**: two issuers that shared a symbol collapse into
  one, and the delisted one disappears from every historical universe. Ticker reuse is
  routine, not exotic — `GM` was reassigned within 18 months.
- **Defaulting a missing settlement price to zero**: a merger that paid $54.20/share
  settles at $0 and the backtest records a 100% loss on a profitable exit. Silent, and
  indistinguishable from a real bankruptcy in the P&L.
- **Treating bankruptcy as an instant zero**: the delisting return from the last
  exchange price is what the position actually realized. Zero is a *modelling choice*
  that must be stated, not the default truth.
- **Imputing over an observed delisting return**: the −30%/−55% figures replace a
  *missing* performance-related delisting return. Applying them where CRSP reports an
  actual `DLRET` overwrites data with an estimate — and they are estimates from US
  equity samples of the 1962–1993 era, not constants for other venues or asset classes.
- **Mixing the two delisting-date conventions**: loading an index deletion date as a
  last trading date holds every deleted name one extra session, every time.
- **Certifying bias protection on a single delisted name**: "at least one delisted
  instrument" is satisfiable by one name in a universe of 500. Set an attrition
  threshold you are willing to defend, and record it with the audit.
- **Deleting rather than settling**: dropping the position when the ticker stops
  printing removes the loss. The name is in the universe and the bias is still there.

## Verification

- Register AAPL (active), LEH (last traded 2008-09-16, bankruptcy) and TWTR (last
  traded 2022-10-27, merger at $54.20). Confirm `get_active_universe(2008-01-01)` is
  `['AAPL', 'LEH']` and `get_active_universe(2020-01-01)` is `['AAPL', 'TWTR']`.
- Boundary: `TWTR` must be in the 2022-10-27 universe and out of the 2022-10-28 one.
  Off by one in either direction is a real bug — a whole session of a name that traded,
  or a session holding a name that did not.
- Register both General Motors issuers under distinct `security_id`s and confirm the
  registry holds 2 records, `GM` is in the 2008 universe, `resolve_symbol('GM', 2008-01-01)`
  is the Corporation and `resolve_symbol('GM', 2015-01-01)` is the Company, and
  `resolve_symbol('GM', 2010-01-01)` is `None`.
- Settlement values, derived independently: TWTR 100 shares → `$5,420.00`
  (100 × $54.20), **not** `$5,370.00` (100 × the $53.70 last close). LEH 100 shares at
  a $3.65 last price with the −30% imputation → `$255.50`.
- Negative checks — each must raise `UniverseError`: settling an ACTIVE security;
  registering a delisted security with no terminal value; registering one with both;
  `delisting_date` before `listing_date`; a delisting reason with no date; a date with
  reason `ACTIVE`; a negative settlement price; a `delisting_return` below −1.0
  (while exactly −1.0 is accepted); a non-finite price or quantity; a
  `datetime.datetime` where a `date` is required; a duplicate `security_id`; settling a
  recycled ticker by ticker rather than by id; settling by a string that is both one
  security's id and another's ticker (check both registration orders); an inverted
  audit window.
- Audit: over 2008-01-01..2008-12-31 with those three names, `universe_in_period` is 2
  (TWTR listed in 2013), `never_live_in_period` is 1, and `attrition_rate` is 0.50 —
  not the 0.333 a registry-wide denominator reports.
- Ghost audit: with `current_static_universe=['AAPL']` over 2008..2023, `ghost_count`
  is 2. With no snapshot supplied it is `None`, and must be rendered "not audited".
- Run `python -m unittest discover -s skills/survivorship-bias-free-universe-construction/scripts` and confirm 100%
  pass rate (51 tests).

## Related Skills

- `point-in-time-index-constituent-tracking`
- `backtest-look-ahead-in-universe-selection`
- `corporate-action-adjusted-backtesting`
- `reference-data-symbol-mapping-across-vendors`
- `lookahead-bias-elimination`
- `walk-forward-validation-setup`
