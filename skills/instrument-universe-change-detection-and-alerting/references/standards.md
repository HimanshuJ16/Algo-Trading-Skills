# Standards — instrument-universe-change-detection-and-alerting

## Identifier facts (verified against primary sources)

### FIGI

Source: OMG / Bloomberg (Registration Authority), **Allocation Rules for the Financial
Instrument Global Identifier (FIGI) Standard**, Version 29.9, July 2022
([openfigi.com](https://www.openfigi.com/assets/local/figi-allocation-rules.pdf)).
The FIGI standard is an OMG standard (Sec. 1.2.1) and ANSI ASC X9.145-2021 (Sec. 1.2.2).

| Fact | Location |
|---|---|
| "A FIGI is never reused and remains with the instrument in perpetuity. A FIGI does not change as a result of any corporate action." | Sec. 1.2.1 |
| "FIGI does not change as a result of corporate action. When Ticker Symbol changes, FIGI stays under new Ticker Symbol." | Sec. 3.1 |
| On a ticker change the FIGI is intact and the new ticker is associated with the composite and venue-level FIGIs; the **old ticker is no longer associated with a FIGI** | Sec. 3.2.1 |
| Ticker changes across execution venues change the **exchange code** carried by the composite-level FIGI (e.g. `LI` $\to$ `LN`) | Sec. 3.2.2 |
| **Delisting**: "The FIGI continues to exist for the Composite level, for the original Place of Listing and for each regional exchange/Trading Venue regardless of listing status." | Sec. 3.2.4 |
| Change of company name: "the FIGI will never change" | Sec. 3.2.5 |
| Mergers: FIGI does not change for acquirer or target, including reverse mergers | Sec. 3.2.6 |
| Spin-offs: the parent keeps its FIGI; a **new FIGI is allocated** for a newly created entity | Sec. 3.2.7 |
| "All instruments, active and inactive, are allocated a FIGI." Equities get a FIGI at composite and trading-venue level; share-class level additionally | Sec. 2.1 / 2.2 |
| FIGI structure: 12 characters — 2 upper-case consonants (incl. "Y"), then "G", then 8 consonants/digits, then a numeric check digit | Sec. 1.1.2 |
| Taiwan (`TT`) exchange code is shared by two exchanges, so ticker + exchange code is not a unique key there | Sec. 2.2.2 |

`is_valid_figi()` implements the Sec. 1.1.2 **structure only**. The check-digit algorithm
is not published in the allocation rules, so a structurally valid string is not proof the
FIGI was ever allocated: `BBG000MM82B1` passes the structural test and OpenFIGI returns
"No identifier found" for it. Resolve against the OpenFIGI mapping API when existence
matters.

### ISIN

Source: ANNA, **ISIN Uniform Guidelines 2025** (ISO 6166)
([anna-web.org](https://anna-web.org/wp-content/uploads/2025/11/ISIN-Guidelines-Dec-2025_Amendment_clean.pdf)).

| Fact | Location |
|---|---|
| "The allocation of an ISIN represents the identification of a financial instrument rather than the market a financial instrument trades on (except for options, futures and commodities). Fungible financial instruments will be identified by one ISIN." | Sec. 1.1 |
| "ISINs should never be re-used." | Sec. 6 |
| 12 alphanumeric characters, 2 alpha prefix, final modulus-10 "Double-Add-Double" check digit per ISO 6166 Annex C | Sec. 7 |
| Change of name: for paperless shares and debt, "The ISIN code remains unchanged" | Sec. 4.9 |
| Merger by absorption: the absorbed company's ISIN becomes inactive. Merger by amalgamation: **a new ISIN is allocated** and both former ISINs become inactive | Sec. 4.2 |
| Change of domicile can require a new ISIN where the security is exchanged for a new one | Sec. 4.1 |
| Liquidation: the ISIN becomes inactive after deletion from the commercial register | Sec. 4.8 |

`is_valid_isin()` implements the Sec. 7 / ISO 6166 Annex C check digit and is unit-tested
against ISINs published in the guidelines themselves (FTSE 100 `GB0001383545`,
IBEX 35 `ES0SI0000005`, S&P 500 `US78378X1072`) and resolved via OpenFIGI
(Apple `US0378331005`, Meta `US30303M1027`).

**Consequence for this engine:** FIGI survives every corporate action; ISIN does not.
An ISIN is also not venue-granular — `US30303M1027` (Meta Class A) resolves to 258 FIGI
rows across trading venues in the OpenFIGI mapping API. An ISIN-keyed multi-venue
universe therefore produces duplicate keys, which the engine rejects rather than
silently collapsing.

## Worked reference case: FB $\to$ META

Source: Meta Platforms, Inc., Form 8-K exhibit 99.1, 31 May 2022
([SEC EDGAR](https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/may312022-exhibit991.htm)).

> "Meta Platforms, Inc. (Nasdaq: FB) today announced that its Class A common stock will
> begin trading on NASDAQ under the ticker symbol 'META' prior to market open on June 9,
> 2022. ... The company's Class A common stock will continue to be listed on NASDAQ and
> its CUSIP number will remain unchanged."

The permanent identifiers were unaffected: composite FIGI `BBG000MM2P62`, ISIN
`US30303M1027`. The ticker was not: `FB` now resolves to the ProShares S&P Dynamic Buffer
ETF (`BBG01VRMNFB1`) in OpenFIGI. This is the concrete case for keying diffs on permanent
identifiers and for never assuming a retired ticker stays retired.

## Engineering standards for universe change tracking

| Control | Standard |
|---|---|
| Primary key | Universe diffs MUST be keyed on a permanent identifier (FIGI / ISIN), at one consistent granularity level across both snapshots. |
| Duplicate keys | MUST be rejected, not last-wins-collapsed — a duplicate hides a listing and skews every count. |
| Snapshot credibility | A comparison MUST NOT emit automated liquidations when the current snapshot fails a churn guard (empty file, or deletions above a calibrated fraction of the universe). |
| Delisting action | A transition to `DELISTED` MUST recommend position closure, not a trading freeze. |
| Resumption action | A transition back to `ACTIVE` MUST NOT recommend a freeze. |
| Unknown status | An unrecognised status MUST route to manual review rather than be mapped by guesswork. |
| Ticker rename action | Renames MUST update symbol mapper tables and live subscriptions together, without breaking historical joins keyed on the permanent identifier. |
| Snapshot ordering | Where as-of dates are available they MUST be checked; reversed snapshots invert additions and deletions. |
| Auditability | The suppressed action, the deletion ratio and the counts MUST be preserved in the report for post-incident review. |

## Configuration defaults (calibrate before use)

These are library defaults, **not** industry standards. No regulator or standards body
publishes a maximum tolerable universe churn rate.

| Parameter | Default | What it does |
|---|---|---|
| `id_scheme` | `"OPAQUE"` | No identifier format enforcement. Set `"FIGI"` or `"ISIN"` in production — that is what catches a snapshot accidentally keyed on tickers. |
| `max_deletion_ratio` | `0.10` | Fraction of the previous universe that may disappear before the snapshot is treated as truncated. Calibrated for a universe of hundreds of names; on a 3-name universe one deletion is 33% and always trips it. Tune against your own observed daily churn, including index-rebalance days. |

## Known limitations

- The engine compares two files. It cannot distinguish a delisting from an index removal,
  a vendor scope change, or a truncated extract — only the `status` field and an external
  corporate-action feed can.
- Delistings are invisible unless the vendor maintains `status`, because the delisted row
  keeps its FIGI and normally stays in the master (FIGI Allocation Rules, Sec. 3.2.4).
- Spin-off and merger *linkage* is out of scope: a spin-off appears as an unexplained
  addition with a new FIGI, and a merger as a deletion or delisting of the target.
- The churn guard is a blunt instrument: it holds a genuine mass-delisting event exactly
  as it holds a corrupt file. A suspect report must page a human.

## Category

`reference-data`
