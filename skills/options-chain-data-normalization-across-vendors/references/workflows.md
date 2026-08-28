# Workflows — options-chain-data-normalization-across-vendors

Step-by-step ingest procedure. Field names and allowed values are in
`references/standards.md`; the sign-off list is in `assets/checklist.md`.

## 1. Choose the policy before the first snapshot

```python
engine = OptionsChainNormalizationEngine(NormalizationConfig(
    strict_osi_cross_check=True,        # cross-check vendor OSI vs component fields
    standard_contract_multiplier=100.0, # anything else flags NON_STANDARD_DELIVERABLE
    reject_on_error=True,               # quarantine and continue; False = fail loudly
))
```

Every default is the conservative setting. Relax one only with a stated reason —
`strict_osi_cross_check=False` is defensible when a vendor is known to ship its symbol and
its component fields from different snapshots, and indefensible otherwise.

## 2. Dispatch on the vendor explicitly

`normalize_chain(vendor, records)` resolves the parser from a registry and raises
`NormalizationError` on an unknown vendor. Add a vendor with:

```python
engine.register_parser("TRADIER", my_tradier_parser)
```

Do **not** add a default branch. A default branch means every field lookup for the
unrecognized vendor misses, every miss takes a default, and the chain emerges as
well-formed contracts the vendor never sent — the failure looks like success.

## 3. Translate identity fields, rejecting what cannot be read

Per vendor:

- **Polygon** — if `ticker` is present it is decoded as OSI (with or without the `O:`
  prefix) and cross-checked against `underlying_ticker` / `expiration_date` /
  `contract_type` / `strike_price`. A record carrying only the `ticker` is still parsed:
  the components come from the symbol. `contract_type='other'` is rejected.
- **IBKR** — root from `tradingClass` when present, else `symbol`; expiry from
  `lastTradeDateOrContractMonth` (or the `expiry` shorthand) as `YYYYMMDD`; right from any
  of `P`/`PUT`/`C`/`CALL`; `localSymbol` drives the cross-check. A `YYYYMM` contract month
  is rejected — resolve the last trading day first.
- **Bloomberg** — `"<root> <exchange> MM/DD/YY <C|P><strike> <yellow key>"`. The two-digit
  year expands into the 2000s. The ticker is *not* an OSI string and is not cross-checked
  as one.
- **OPRA** — the OSI symbol is the identity; component fields, when present, are
  cross-checked against it.

## 4. Build the OSI key with the field limits enforced

`Root(6, left-justified, space-padded) + YYMMDD + C/P + Strike×1000 (8 digits)`.

Reject rather than encode:

| Input | Why it is rejected |
|---|---|
| Root longer than 6 characters | Truncation emits a valid symbol for a different contract |
| Strike ≤ 0 | Emits `-` inside the numeric field, still 21 characters |
| Strike > 99,999.999 | Widens the symbol to 22 characters |
| Sub-mill strike (`150.0005`) | Rounds onto a different listed strike |
| Right outside `C`/`CALL`/`P`/`PUT` | Any fallback inverts every Greek on the line |

## 5. Cross-check the vendor's symbol against its own fields

Decode the vendor-supplied OSI string, rebuild the symbol from the vendor's component
fields, and compare. On disagreement flag `OSI_MISMATCH` and prefer **neither** side —
choosing one resolves, and therefore hides, a contradiction inside a single payload. This
is the check most likely to catch a vendor's own upstream bug before it reaches a
position.

## 6. Normalize the quote through one shared routine

Identical for every vendor — per-vendor midpoint rules are precisely what makes two feeds
of one contract disagree by a tick:

1. Map no-data sentinels to absent **before** any arithmetic. Negative prices (IBKR's
   `-1`), `NaN` and `Inf` are all absent.
2. A **zero bid is a real quote** (`0.00 × 0.05` → mid `0.025`, flag `ZERO_BID`). A
   **zero ask is no offer** → `MISSING_QUOTE`.
3. `bid > ask` → flag `INVALID_BID_ASK`, report the **signed** spread, emit **no**
   midpoint.
4. Otherwise `mid = (bid + ask) / 2`, `spread = ask - bid`.
5. Carry `last_price` separately. Never blend it into the midpoint.

## 7. Quarantine failures; keep the chain

```python
report = engine.normalize_chain("POLYGON", records)
for rejected in report.rejected_records:
    dead_letter(rejected.index, rejected.reason, rejected.raw)
```

`total_records_processed == len(normalized_contracts) + len(rejected_records)` always
holds, so a partially rejected chain cannot be mistaken for a complete one.

Do **not** retry a rejected record unchanged: rejection means the payload was unreadable,
and re-parsing identical bytes yields an identical rejection. Alert on the rejection
*rate* — a sudden rise is almost always a vendor schema change, which is the failure this
design exists to surface.

## 8. Consume the report

- `quality_status` — one worst-first string; see the precedence table in
  `references/standards.md`.
- `flag_counts` — every observation, including the ones that deliberately do not degrade
  the status (`ZERO_BID`, `NON_STANDARD_DELIVERABLE`).
- Per contract, gate execution on `contract.is_quotable` (a usable, uncrossed two-sided
  midpoint) rather than on `mid_price is not None` scattered through strategy code.
- Treat `NON_STANDARD_DELIVERABLE` as a routing decision, not an error: the series is
  tradable, but its deliverable and multiplier are not the standard 100 shares, so
  position sizing and margin must use `contract_multiplier`.
