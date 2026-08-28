# Symbology & Vendor Field Reference — options-chain-data-normalization-across-vendors

All field names and allowed values below are taken from the vendors' own published
documentation; see Sources at the end. Where a third-party wrapper disagrees with the
vendor, trust the vendor.

## OSI 21-character symbol layout

| Field | Width | Content | Justification |
|---|---|---|---|
| Root | 6 | Underlying/OCC root, alphanumeric | Left, **space**-padded |
| Expiration | 6 | `YYMMDD` | — |
| Right | 1 | `C` or `P` | — |
| Strike | 8 | Strike × 1000 (5 dollar digits + 3 mill digits) | Right, **zero**-padded |

Published examples this skill's tests reproduce exactly:

| Symbol | Meaning |
|---|---|
| `AAPL  240119C00150000` | AAPL $150.00 call, 2024-01-19 |
| `SPX   141122P01950000` | SPX $1,950.00 put, 2014-11-22 |
| `LAMR  150117C00052500` | LAMR $52.50 call, 2015-01-17 |

Consequences the implementation enforces rather than assumes:

- **Representable strike range is `(0, 99999.999]` in whole mills.** The field holds 5
  dollar digits and 3 mill digits. A larger strike widens the symbol to 22 characters; a
  negative one emits `-` inside the numeric field while keeping the total at 21, so a
  length check alone will not catch it. Sub-mill precision (`150.0005`) cannot be encoded
  and must not be rounded — the nearest mill is a *different listed contract*.
- **The root field is 6 bytes.** A longer root is invalid input, not something to
  truncate: `GOOGLE1` sliced to `GOOGLE` is a well-formed symbol for another contract.
- **The year has no century.** OSI post-dates the February 2010 cutover, so `YY` resolves
  into the 2000s.

## Adjusted and non-standard roots

| Root form | Meaning |
|---|---|
| `AAPL` | Standard series, standard deliverable |
| `AAPL1`, `AAPL2`, … | OCC adjustment suffix: non-standard deliverable after a corporate action. The suffix identifies the series as non-standard; it does **not** encode how it was adjusted. |
| `AAPL7` | Mini option (10 shares per contract rather than 100) |

The OSI root and the underlying ticker are therefore **not the same field**. This skill
carries both (`osi_root`, `underlying_ticker`) and flags
`NON_STANDARD_DELIVERABLE` when they differ, when the multiplier is not the configured
standard 100, or when the vendor reports additional deliverables.

## Vendor field mapping

| Vendor | Contract identifier | Underlying | Expiration | Right | Strike | Multiplier |
|---|---|---|---|---|---|---|
| Polygon.io | `ticker` — `O:AAPL240119C00150000` (OSI, `O:` prefix, unpadded root) | `underlying_ticker` | `expiration_date` (`YYYY-MM-DD`) | `contract_type` — `put` / `call` / `other` | `strike_price` | `shares_per_contract` |
| Interactive Brokers | `localSymbol` — "For options, this will be the OCC symbol" | `symbol` (underlying) + `tradingClass` (OSI root) | `lastTradeDateOrContractMonth` — `YYYYMMDD` = last trading day, `YYYYMM` = contract month | `right` — `P` / `PUT` / `C` / `CALL` | `strike` | `multiplier` |
| Bloomberg | `AAPL US 01/19/24 C150 Equity` — root, exchange, `MM/DD/YY`, right+strike, yellow key | root component | `MM/DD/YY` component | `C` / `P` component | numeric suffix on the right component | — |
| OPRA | OSI symbol (OPRA disseminates under OSI symbology) | root component | `YYMMDD` component | `C` / `P` component | `Strike × 1000` component | — |

Two values in that table are the ones most likely to be mishandled:

- **Polygon's `contract_type` has a third value.** It is documented as "'put', 'call', or
  in some rare cases, 'other'". `other` is rejected here. Any `else: PUT` fallback turns
  it into a put.
- **IBKR's `right` has four spellings, not two.** `P`, `PUT`, `C`, `CALL`. A test for
  `== "C"` classifies the documented value `CALL` as a put.
- **IBKR's expiration field is overloaded.** "Strings with format YYYYMM will be
  interpreted as the Contract Month whereas YYYYMMDD will be interpreted as Last Trading
  Day." A contract month names no single expiration date and is rejected here rather than
  guessed at.

## Quote normalization rules

| Input | `bid` | `ask` | `mid_price` | `spread` | Flag |
|---|---|---|---|---|---|
| `5.20 / 5.40` | 5.20 | 5.40 | 5.30 | 0.20 | — |
| `0.00 / 0.05` | 0.0 | 0.05 | 0.025 | 0.05 | `ZERO_BID` |
| `5.30 / 5.30` (locked) | 5.30 | 5.30 | 5.30 | 0.00 | — |
| `6.00 / 5.00` (crossed) | 6.00 | 5.00 | `None` | **-1.00** | `INVALID_BID_ASK` |
| `-1 / -1` (IBKR no data) | `None` | `None` | `None` | `None` | `MISSING_QUOTE` |
| `0.00 / 0.00` | 0.0 | `None` | `None` | `None` | `MISSING_QUOTE` |
| `NaN` / `Inf` | `None` | — | `None` | `None` | `MISSING_QUOTE` |

`last_price` carries the vendor's last trade separately and is never blended into
`mid_price`.

## Expiration-date conventions across the 2015 boundary

Before February 2015 most standard contracts expired at 11:59 pm ET on the **Saturday**
following the third Friday. The OCC moved standard expiration to the third Friday
effective 1 February 2015, with certain grandfathered expiration dates still falling on a
Saturday after that date. Vendors differ on which date they encode in the OSI date field
for legacy series, so two vendors' pre-2015 history may not join on the OSI key even when
both are internally correct. Reconcile that era on
`(root, right, strike, expiry ± 1 day)` or normalize the date explicitly per vendor.

## Chain status precedence

Worst-first; `flag_counts` and `rejected_records` always carry the full picture.

| Status | Condition |
|---|---|
| `RECORDS_REJECTED` | Any record failed to parse |
| `SYMBOLOGY_MISMATCH` | Any `OSI_MISMATCH` |
| `INVALID_QUOTE_DETECTED` | Any `INVALID_BID_ASK` |
| `DEGRADED_QUOTES` | Any `MISSING_QUOTE` |
| `DATA_INTEGRITY_OK` | None of the above |

`ZERO_BID` and `NON_STANDARD_DELIVERABLE` never degrade the status: most strikes in a
real chain are bid-less, and a status that read `DEGRADED` on every snapshot would be
ignored by the people it is meant to alert.

## Sources

- OCC Options Symbology Initiative field layout (root 6 / `YYMMDD` / `C`|`P` / strike ×
  1000 zero-padded to 8): https://en.wikipedia.org/wiki/Option_symbol —
  see also Fidelity's OSI summary: https://www.fidelity.com/research/options/osi.shtml
- Polygon.io options contract fields, including `contract_type` "'put', 'call', or in
  some rare cases, 'other'":
  https://polygon.io/docs/rest/options/contracts/contract-overview
- IBKR TWS API `Contract` — `right` "Valid values are P, PUT, C, CALL";
  `lastTradeDateOrContractMonth` `YYYYMM` vs `YYYYMMDD`; `localSymbol` "For options, this
  will be the OCC symbol":
  https://interactivebrokers.github.io/tws-api/classIBApi_1_1Contract.html
- IBKR TWS API market data — "When IBApi::EWrapper::tickPrice and IBApi::EWrapper::tickSize
  are reported as -1, this indicates that there is no data currently available":
  https://interactivebrokers.github.io/tws-api/md_receive.html
- IBKR TWS API basic contracts — trading class disambiguating "many option contracts with
  an almost identical description":
  https://interactivebrokers.github.io/tws-api/basic_contracts.html
- OCC move of standard expiration from Saturday to the third Friday, effective 1 February
  2015 (SR-OCC-2013-04):
  https://www.federalregister.gov/documents/2013/06/21/2013-14793/self-regulatory-organizations-the-options-clearing-corporation-order-approving-proposed-rule-change
- OCC contract adjustments and non-standard deliverables (numeric root suffixes):
  https://www.fidelity.com/learning-center/investment-products/options/contract-adjustments
