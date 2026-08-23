# Workflows for Currency Pair Quoting Convention Normalization

1. **Symbol Parsing**:
   - Accept `/`, `_`, `-`, `.`, `:`, whitespace, and the bare six-character form.
   - Reject any leg that is not three alphabetic characters. `USDT/EUR` must
     fail loudly rather than be mis-split; `123456` must not become `123/456`.
   - Reject a symbol naming the same currency on both legs.
2. **Price Validation** (before any branch):
   - Reject non-finite and non-positive prices on the standard path as well as
     the inversion path. A NaN bid otherwise yields a NaN spread that compares
     False against every downstream threshold.
3. **Ranking**:
   - Default ranking: `EUR` > `GBP` > `AUD` > `NZD` > `USD` > `CAD` > `CHF` >
     `JPY`. This is a de-facto market convention, **not** an ISO 4217 artifact —
     see `references/standards.md`.
   - Both legs ranked and in order → `STANDARD`, pass through.
   - Both legs ranked and out of order → `INVERTED`.
   - Either leg unranked → `UNCLASSIFIED`. **Do not invert.** Log a warning and
     pass the vendor's quote through unchanged; extend `priority_list` to have
     the currency ranked.
4. **Inversion Logic** (`INVERTED` only):
   - $\text{Bid}_{\text{std}} = 1 / \text{Ask}_{\text{inv}}$,
     $\text{Ask}_{\text{std}} = 1 / \text{Bid}_{\text{inv}}$.
   - Never same-side invert — it narrows or negates the spread.
5. **Pip Sizing**:
   - Keyed on the **normalized** terms currency, not the raw one.
   - `pip_size_overrides[symbol]` first, then `0.01` if the terms currency is in
     `two_decimal_terms_currencies` (default `{JPY}`), else `0.0001`.
   - `UNCLASSIFIED` with no override → `pip_size = None`,
     `spread_pips = None`. Report `spread_price` instead; it needs no convention.
6. **Spread and Flags**:
   - $\text{Spread Pips} = (\text{Ask}_{\text{std}} - \text{Bid}_{\text{std}}) /
     \text{Pip Size}$, computed from the unrounded published prices so the
     report is self-consistent.
   - Set `is_crossed` when bid exceeds ask. Inversion preserves crossing, so the
     flag always reflects vendor data, never a normalization artefact.
7. **Downstream Gate**:
   - Require `classification == "STANDARD"` or `"INVERTED"` before feeding a
     quote to pricing or execution. `is_inverted == False` alone does not
     distinguish a verified pair from an unrankable one.
