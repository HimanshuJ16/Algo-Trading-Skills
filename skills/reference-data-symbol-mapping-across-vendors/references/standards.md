# Standards for Reference Data Symbol Mapping Across Vendors

The cross-reference table is a *temporal* structure. Every rule below follows from the
fact that a vendor symbol identifies an instrument only within a window, and that one
instrument can carry several symbols at one vendor at the same time.

| Rule | Engineering standard |
|---|---|
| Key normalisation | Lookup keys MUST be trimmed, internal whitespace runs collapsed, and upper-cased unless the vendor namespace is genuinely case-sensitive. |
| Value fidelity | Stored symbols MUST be returned verbatim. Normalisation MUST NOT be applied to a returned value. |
| Point-in-time | Resolution MUST take an `as_of` date. Omitting it MUST mean "currently effective", never "most recently registered". |
| Window semantics | Validity windows are half-open, `[effective_from, effective_to)`, so a changeover is one date with no gap and no overlap. |
| Reverse determinism | Exactly one entry per (canonical, vendor, overlapping window) MUST be `is_primary`. Reverse lookup returns that one; all others MUST remain reachable. |
| Conflict handling | A registration that would make a key resolve two ways over an overlapping window MUST raise by default. Where a conflict is admitted, the first registration MUST win and the conflict MUST be reported. |
| Input validation | Blank or non-string symbol fields MUST be rejected at registration, not stored. |

**Forward and reverse are not symmetric in general.** `forward_lookup(reverse_lookup(c, v), v) == c`
holds; the mirror image does not, because a vendor carries more than one symbol per
instrument and only the primary one comes back. Any check asserting full symmetry will
fail on correct data.

---

## Vendor symbologies this skill maps between

### Bloomberg ticker

`<ticker> <exchange code> <yellow key>` — `AAPL US Equity`. The exchange code is the part
that matters here, and there are two of them for the same security:

| Bloomberg ticker | Exchange code field | Meaning |
|---|---|---|
| `AAPL US Equity` | `EQY_PRIM_SECURITY_COMP_EXCH` | **Composite** — pricing consolidated across eligible US market participants |
| `AAPL UW Equity` | `EQY_PRIM_SECURITY_PRIM_EXCH` | **Primary exchange** — Nasdaq specifically |

Both are correct, both are live, and they are not interchangeable. This is the canonical
case for `is_primary`: designate the one your routing uses, register the other as a
secondary so inbound data still resolves.

Source: <https://assets.bbhub.io/professional/sites/10/Bloomberg-US-Domestic-Equity-Indices-Methodology.pdf>

### RIC — Refinitiv (formerly Reuters) Identification Code

RIC root (one to four characters) + `.` + a one- or two-character exchange code:
`AAPL.O` is Apple on Nasdaq, `IBM.N` is IBM on the NYSE, `IBM.L` the same issuer's London
line. **The root alone is not a mapping key** — it does not name a listing. A cross-listed
issue has one ISIN and a RIC per venue.

Source: <https://en.wikipedia.org/wiki/Refinitiv_Identification_Code>

### Structured identifiers (ISIN, CUSIP, SEDOL, FIGI)

Treated here as opaque vendor strings. Their syntax, check-digit algorithms and
granularity — one ISIN per *issue*, one SEDOL per security *per market* — belong to
`isin-cusip-sedol-cross-reference-service`. Validate at the ingest boundary, then register.

---

## Why effective dating is not optional

**Tickers are recycled.** The NYSE ticker `S` has had at least three owners:

| Window | Issuer | Evidence |
|---|---|---|
| … to 13 April 2020 | Sprint Corporation | The T-Mobile merger became effective 1 April 2020; the NYSE removed Sprint's entire class of securities from listing and registration at the opening of business on 13 April 2020 (SEC Form 25-NSE). |
| 13 Apr 2020 – 30 Jun 2021 | *nobody* | The symbol resolves to nothing. A lookup here must miss, not answer. |
| from 30 June 2021 | SentinelOne, Inc. | Listed on the NYSE under `S` at its IPO, priced 29 June 2021. |

Sources: <https://www.sec.gov/Archives/edgar/data/101830/000087666120000282/ruleprovisionnotice.htm> ·
<https://www.sentinelone.com/press/sentinelone-announces-pricing-of-initial-public-offering/>

An undated table keyed on `("NYSE", "S")` gives whichever issuer was registered last. The
failure is silent: a backtest over 2019 data attributes Sprint's prints to SentinelOne and
produces a clean-looking equity curve for a company that was not listed.

**Renames move the ticker and nothing else.** Meta Platforms' Class A common stock began
trading under `META` before market open on 9 June 2022, replacing `FB`. The listing
continued on Nasdaq and **the CUSIP was unchanged**. Two consequences:

- Key the canonical symbol on something that does not move — a FIGI or an internal
  surrogate — and carry the ticker as a dated attribute. Keying on the ticker turns a
  rename into a delisting plus a new instrument and splits position history in two.
- Model the change as `retire_mapping(..., 2022-06-09)` plus a new entry
  `effective_from=2022-06-09`. Half-open windows abut exactly: no day resolves twice, no
  day resolves to nothing.

Source: <https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/may312022-exhibit991.htm>

---

## What this table does not tell you

It records what somebody registered. It does not poll a vendor, an exchange notice or a
corporate-action feed, so a symbology change nobody entered is a mapping that stays wrong
and never warns. Coverage reporting catches *absent* mappings, not *stale* ones — staleness
is only detectable against a change feed (`reference-data-change-notification-pipeline`,
`instrument-universe-change-detection-and-alerting`).

Note also that RIC and Bloomberg ticker strings are licensed vendor symbology. Whether an
internal cross-reference table may hold and redistribute them is a contractual question,
not a technical one; see `data-vendor-contractual-usage-restriction-tracking`.
