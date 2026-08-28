# Pre-Flight / Sign-off Checklist — options-chain-data-normalization-across-vendors

Use this before considering an options-chain ingest complete.

## Symbology

- [ ] **21 Characters, Always:** Every emitted `standard_osi_symbol` is exactly 21
      characters. A 22-character symbol means a strike overflowed the field.
- [ ] **Root Space-Padded, Strike Zero-Padded:** Root left-justified in 6 bytes padded
      with **spaces**; strike right-justified in 8 digits padded with **zeros**.
- [ ] **Strike In Range And In Mills:** `0 < strike <= 99999.999`, whole mills. A
      sub-mill strike is rejected, never rounded onto the neighbouring listed contract.
- [ ] **Over-Long Roots Rejected:** No root is silently sliced to 6 characters.
- [ ] **Published Examples Reproduce:** `SPX   141122P01950000` and
      `LAMR  150117C00052500` build byte-for-byte.

## Contract identity

- [ ] **OSI Root ≠ Underlying Ticker:** Both are carried. Adjusted (`AAPL1`) and mini
      (`AAPL7`) roots come from `tradingClass` / the vendor symbol, not from `symbol`.
- [ ] **Non-Standard Deliverables Flagged:** `NON_STANDARD_DELIVERABLE` is raised on a
      suffix mismatch, a multiplier other than 100, or reported additional underlyings —
      and position sizing uses `contract_multiplier`, not an assumed 100.
- [ ] **Round-Trip Cross-Check On:** `strict_osi_cross_check=True`, or a written reason
      why not. `OSI_MISMATCH` is investigated, not suppressed.
- [ ] **Right Parsed Totally:** `P`, `PUT`, `C`, `CALL` all map correctly; anything else
      — including Polygon's documented `contract_type='other'` — is rejected, never
      defaulted to a put.
- [ ] **Expiry Is A Date, Not A Month:** IBKR `YYYYMM` contract months are resolved to a
      last trading day before normalizing.

## Quotes

- [ ] **Sentinels Mapped Before Arithmetic:** IBKR's `-1`, `NaN` and `Inf` become absent
      before any midpoint is computed. No contract carries a negative `mid_price`.
- [ ] **Zero Bid Preserved:** `0.00 × 0.05` yields `mid_price = 0.025` and `ZERO_BID` —
      not a last-trade substitute, and not a missing quote.
- [ ] **Zero Ask Is No Offer:** `ask = 0` yields `MISSING_QUOTE`.
- [ ] **Spread Signed:** A crossed book reports a negative spread and **no** midpoint.
      Nothing clamps with `max(0, ask - bid)`.
- [ ] **Last Trade Never Blended:** `last_price` is a separate field; `mid_price` is
      `(bid + ask) / 2` or `None`.
- [ ] **One Routine, Every Vendor:** The same contract from two vendors produces the same
      midpoint and spread. Verified with an actual cross-vendor test, not by inspection.

## Ingest robustness

- [ ] **Vendor Dispatch Is Explicit:** An unregistered vendor raises. There is no default
      parser branch.
- [ ] **Nothing Defaulted:** No missing underlying, expiry, strike or right is filled in.
      Grep the parsers for `.get(key, <literal>)` on any identity field.
- [ ] **Records Quarantined, Chain Kept:** One malformed record does not discard the
      snapshot; `total_records_processed == normalized + rejected`.
- [ ] **Rejections Dead-Lettered And Rate-Alerted:** Rejected payloads are retained and
      the rejection *rate* is monitored — a spike is usually a vendor schema change.
- [ ] **Rejections Not Retried Unchanged:** Re-parsing identical bytes yields an identical
      rejection.

## History and joins

- [ ] **2015 Boundary Checked:** For chains predating February 2015, the Friday/Saturday
      expiration convention has been confirmed per vendor before joining on the OSI key.
- [ ] **Cross-Vendor Join Verified:** The same contract from two vendors resolves to one
      OSI key on a real sample, not just on the happy-path fixture.
- [ ] **Status Read With Flags:** Consumers read `flag_counts` and `rejected_records`, not
      `quality_status` alone.
