---
name: options-chain-data-normalization-across-vendors
description: >-
  Use when options chains arrive from several vendors under different symbology, date
  formats and sentinel values, mapping them to the OCC 21-character standard with
  mid-prices and an integrity audit.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: options-normalization, osi-symbology, occ-format, options-chain, ibkr-api, polygon-options, bloomberg-options, market-data
  brokers_frameworks: "OCC Option Symbology Initiative (OSI); Polygon.io Options API; Interactive Brokers TWS API; Bloomberg Ticker Convention; OPRA; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when ingesting options chain market data across multiple brokers and
vendors (Polygon.io, Interactive Brokers, Bloomberg, OPRA). Vendor feeds deliver the same
contract under different symbology, date representations, field names and sentinel
values. This engine resolves those disagreements once, at the ingest boundary, into a
single OCC 21-character Option Symbology Initiative (OSI) key
(`AAPL  240119C00150000`) plus one canonical quote, so nothing downstream branches per
vendor.

Two properties of this implementation are load-bearing, and both are the opposite of the
obvious approach:

- **Nothing is defaulted.** Every unreadable field raises `NormalizationError` and the
  record is quarantined. In options data a defaulted field does not produce an obvious
  error — it produces a *different, real, tradable contract* that joins cleanly against
  position and risk tables, and no downstream check can detect it.
- **`mid_price` is `(bid + ask) / 2` or `None`.** It is never synthesised from the last
  trade. A midpoint invented for a series with no market reports a tradable price for
  exactly the series where a marketable order does the most damage.

## When NOT to Use

- **As a pricing or Greeks layer.** This produces a quote midpoint, not a theoretical
  value. Fitting a surface belongs in
  `options-implied-volatility-surface-construction`; backtesting against one belongs in
  `options-backtesting-with-realistic-iv-surface`.
- **As a staleness, sequencing or gap detector.** The engine holds no cross-snapshot
  state. It will happily normalize the same stale snapshot forever. Use
  `sequence-number-gap-detection-for-feeds` and `model-staleness-detection`.
- **To arbitrate between two vendors quoting the same contract.** This normalizes each
  vendor independently and flags internal contradictions; it does not decide which
  vendor is right. That is `multi-source-price-reconciliation-tie-breaking`.
- **On historical chains spanning February 2015 without checking expiration
  conventions.** Before that date most standard contracts expired on the *Saturday*
  following the third Friday, and vendors differ on whether they encode the Friday or the
  Saturday in the OSI date field for those legacy series. Two vendors' history will not
  join on the OSI key across that boundary — see Common Pitfalls.
- **On non-US listed options.** OSI is the OCC's US convention. Nothing here maps
  Eurex, NSE or HKEX contract identifiers; see
  `options-chain-expiry-cycle-conventions-by-exchange`.

## Prerequisites

- A decoded vendor payload as a `Mapping` (this module does no transport or HTTP).
- The vendor's name, matching a registered parser: `POLYGON`, `IBKR`, `BLOOMBERG`,
  `OPRA`, or one added via `register_parser()`. An unregistered vendor raises — it does
  not fall through to a default parser.
- For IBKR: the contract's **last trading day** (`YYYYMMDD`). IBKR documents
  `lastTradeDateOrContractMonth` as accepting `YYYYMM` for a contract month, which names
  no single expiration and is rejected.
- A decision on `NormalizationConfig`: `strict_osi_cross_check` (default `True`),
  `standard_contract_multiplier` (default `100.0`), `reject_on_error` (default `True`,
  i.e. quarantine and continue).

## Workflow

1. **Dispatch on the vendor explicitly.** `normalize_chain(vendor, records)` looks the
   parser up in a registry and raises when the vendor is unknown. Falling through to a
   "default" parser is how a Bloomberg chain gets read with Polygon's field names: every
   lookup misses, every miss takes a default, and the result is a full chain of
   well-formed contracts that the vendor never sent.

2. **Translate each vendor's identity fields, rejecting what cannot be read:**
   - **Polygon**: `ticker` = `O:AAPL240119C00150000` plus `underlying_ticker`,
     `expiration_date` (`YYYY-MM-DD`), `contract_type`, `strike_price`. `contract_type`
     is documented as `put`, `call`, **or in rare cases `other`** — `other` is rejected,
     not silently classified as a put.
   - **IBKR**: `symbol`, `lastTradeDateOrContractMonth`, `right`, `strike`. `right` is
     documented as taking `P`, `PUT`, `C`, **or `CALL`** — all four are parsed. Prefer
     `tradingClass` for the OSI root and `localSymbol` (documented as the OCC symbol) for
     the cross-check.
   - **Bloomberg**: `AAPL US 01/19/24 C150 Equity` — root, exchange, `MM/DD/YY`, right
     and strike, yellow key.
   - **OPRA**: already OSI; the symbol *is* the identity.

3. **Build the 21-character OSI key and reject anything the fields cannot hold.**
   `Root(6, left-justified, space-padded) + YYMMDD + C/P + Strike×1000 (8 digits)`. The
   strike field is 5 dollar digits plus 3 mill digits, so a strike outside
   `(0, 99999.999]` or one carrying sub-mill precision is rejected rather than encoded
   into a symbol of the wrong length or onto a different listed strike. A root longer
   than the 6-byte field is rejected rather than truncated.

4. **Cross-check the vendor's own OSI string against the OSI rebuilt from its own
   component fields.** A disagreement is flagged `OSI_MISMATCH` and neither side is
   preferred — preferring one would resolve, and therefore hide, a real contradiction
   inside a single payload. This round-trip is the highest-value single check in a
   cross-vendor options normalizer.

5. **Normalize the quote through one shared routine, for every vendor.**
   `mid = (bid + ask) / 2`, `spread = ask - bid` **signed**. Map each vendor's no-data
   sentinel to "absent" *before* the arithmetic; treat a zero bid as a real quote and a
   zero ask as no offer; emit no midpoint from a crossed book. Carry the vendor's last
   trade in `last_price`, never blended into the midpoint.

6. **Quarantine, don't abort.** A record that fails any of the above lands in
   `report.rejected_records` with its reason and raw payload while the rest of the chain
   normalizes; `total_records_processed` always equals normalized + rejected, so a
   partially rejected chain cannot be mistaken for a complete one. Set
   `reject_on_error=False` for a batch job that must fail loudly instead.

7. **Read the flags, not just the status.** `quality_status` collapses the chain to one
   worst-first string (`RECORDS_REJECTED` → `SYMBOLOGY_MISMATCH` →
   `INVALID_QUOTE_DETECTED` → `DEGRADED_QUOTES` → `DATA_INTEGRITY_OK`);
   `report.flag_counts` carries every observation. `ZERO_BID` and
   `NON_STANDARD_DELIVERABLE` deliberately never degrade the status, because both are
   ordinary properties of a healthy chain.

> Full procedure: see `references/workflows.md`.
> Vendor field mapping and symbology reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Classifying the option right with a prefix test.** `'C' if right.startswith('C')
  else 'P'` looks total and is not: IBKR documents `right` as "Valid values are P, PUT,
  C, CALL", so the literal string `'CALL'` — a documented value — becomes a **put**, and
  Polygon's documented `contract_type='other'` becomes a put too. Every Greek on that
  line then carries the wrong sign, and the contract still prices, still quotes and still
  reconciles against itself.

- **Averaging IBKR's `-1`.** IBKR documents that "When IBApi::EWrapper::tickPrice and
  IBApi::EWrapper::tickSize are reported as -1, this indicates that there is no data
  currently available." A `-1` bid against a `-1` ask satisfies `bid <= ask`, so the
  contract passes every integrity check while carrying `mid_price = -1.0` and
  `spread = 0.0` — a negative price on a chain reporting `DATA_INTEGRITY_OK`.

- **Treating a zero bid as a missing quote.** `0.00 × 0.05` is the normal state of every
  deep out-of-the-money series. Its midpoint is 0.025. Substituting the last trade price
  there replaces a live quote with a print that may be days old, and — because vendors
  differ in when they apply the substitution — makes two feeds of the same contract
  disagree. A zero *ask* is the opposite case: nobody offers at zero, so that is a
  missing quote.

- **Clamping the spread at zero.** `max(0.0, ask - bid)` on a crossed book hides the size
  of the inversion in the one field an integrity audit would inspect, and reports a
  perfectly tight market when the book is broken. Report the signed spread and refuse to
  emit a midpoint the book cannot trade at.

- **Defaulting the underlying, expiry or strike.** A missing field defaulted to
  `"AAPL"`, `"2024-01-19"` and `0.0` produces `AAPL  240119C00000000` — a syntactically
  perfect symbol that will join against reference data and appear in risk reports as a
  real position. Unlike a crash, nothing downstream can detect it.

- **Building the OSI root from the underlying ticker.** After a corporate action the OCC
  appends a numeric suffix (`AAPL1`) to mark a non-standard deliverable, and mini options
  carry `7`. IBKR keeps `symbol` as the underlying and exposes the distinction through
  `tradingClass`; its own docs note that "It is not unusual to find many option contracts
  with an almost identical description (i.e. underlying symbol, strike, last trading
  date, multiplier, etc.)". Build from `symbol` and you name the standard series while
  quoting the adjusted one — same strike, same expiry, different deliverable.

- **Truncating an over-long root or overflowing the strike field.** Slicing a root to 6
  characters emits a valid symbol for a different contract. `f"{mills:08d}"` silently
  widens to 9 digits above $99,999.999 (a 22-character symbol) and emits `-0150000` for a
  negative strike — 21 characters, so a length check alone will not catch it.

- **Joining pre-2015 history on the OSI date.** Before February 2015 most standard
  contracts expired on the Saturday following the third Friday; the OCC moved standard
  expiration to the Friday, with certain grandfathered series still expiring Saturday
  after that date. Vendors differ on which date they encode for legacy series, so a
  historical chain from two vendors will not join on the OSI key across that boundary
  even when both are correct. Reconcile on `(root, right, strike, expiry ± 1 day)` for
  that era, or normalize the date explicitly per vendor.

- **Aborting the chain on one bad record.** An uncaught `strptime` on a single malformed
  expiry discards every good contract in the snapshot. Quarantine the record, keep the
  chain, and alert on the rejection *rate* — a sudden rise is usually a vendor schema
  change, which is the failure this design exists to make visible.

## Verification

- Build `AAPL  240119C00150000` and `NVDA  240621P00450000` and confirm both are exactly
  21 characters. Confirm the independently published OCC examples `SPX   141122P01950000`
  and `LAMR  150117C00052500` reproduce exactly, including the mill digits on the $52.50
  strike (`test_published_occ_examples_reproduce_exactly`).
- Confirm **all four vendors agree**: the same contract expressed as a Polygon record, an
  IBKR record, a Bloomberg ticker and an OPRA symbol yields one OSI key, one midpoint and
  one spread (`test_all_four_vendors_agree_on_the_same_contract`).
- Confirm `right='CALL'` normalizes to `CALL` on the IBKR path and that
  `contract_type='other'` is rejected on the Polygon path.
- Confirm an IBKR `-1` bid/ask yields `mid_price=None` and `MISSING_QUOTE`, not `-1.0`
  under `DATA_INTEGRITY_OK`.
- Confirm `bid=0.00, ask=0.05` yields `mid_price=0.025` and `ZERO_BID` even when a
  `close` of 9.99 is present, and that the same input gives the same midpoint on both
  vendor paths.
- Confirm an inverted quote yields `INVALID_BID_ASK`, a **signed** `spread=-1.00`, and no
  midpoint.
- Confirm a missing underlying, expiry or strike raises rather than producing a defaulted
  AAPL contract; that a root over 6 characters, a strike above $99,999.999 and a negative
  strike each raise; and that one malformed record among three leaves the other two
  normalized with `total_records_processed == normalized + rejected`.
- Confirm an unknown vendor raises instead of falling through to another parser.
- Run `python -m unittest discover -s skills/options-chain-data-normalization-across-vendors/scripts`
  and confirm all tests pass.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `options-chain-expiry-cycle-conventions-by-exchange`
- `options-implied-volatility-surface-construction`
- `options-backtesting-with-realistic-iv-surface`
- `multi-source-price-reconciliation-tie-breaking`
- `corporate-action-event-calendar-integration`
- `multi-exchange-feed-normalization`
