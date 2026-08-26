# Workflows for JPX / TSE arrowhead4.0 Integration

1. **Securities Code Audit**:
   - Normalise to four uppercase characters and match
     `^[0-9][0-9ACDFGHJKLMNPRSTUWXY][0-9][0-9ACDFGHJKLMNPRSTUWXY]$`.
   - Accept both legacy numeric codes (`7203`) and the alphanumeric codes SICC
     has assigned since 1 January 2024 (`130A`, `9A76`). Raise on anything else
     — an ISIN, a five-character code, or a letter in position 1 or 3.
2. **Tick Table Selection (per issue, not per price)**:
   - `TOPIX500` — TOPIX500 constituents, plus ETFs/ETNs/leveraged products with
     a trading unit of 10 or above.
   - `ETF_SINGLE_UNIT` — ETFs/ETNs/leveraged products with a trading unit of 1.
   - `OTHER` — all remaining domestic stocks.
   - Source membership from TSE's per-issue "Handling of Tick Sizes" notices,
     not from an index constituent snapshot.
3. **Tick Size Audit (呼値の単位)**:
   - Select the band from the **order price**, with **inclusive** upper bounds:
     a price of exactly JPY 3,000 takes the tick of the "up to 3,000" band.
   - Verify the price is an exact multiple of the tick, in decimal arithmetic.
     The minimum tick is JPY 0.1, which has no exact binary representation.
4. **Trading Unit Audit (売買単位)**:
   - Verify `quantity` is a strictly positive multiple of the issue's trading
     unit: 100 shares for domestic stocks, 1 or 10 for ETFs/ETNs/REITs.
5. **Daily Price Limit Audit (制限値幅)**:
   - Validate the base price (基準値段) is finite and strictly positive before
     using it as a band anchor.
   - Select the absolute-yen limit from the base price, with **exclusive** upper
     bounds: a base price of exactly JPY 100 falls into the ±JPY 50 band.
   - Accept prices in `[base - limit, base + limit]` inclusive — the stop-high
     and stop-low prices are tradeable.
   - Where TSE has broadened the limit for the issue, pass the published figure
     as an override rather than trying to derive it.
6. **Audit Report Generation**:
   - Emit the applied tick table, tick size, unit count, limit amount and both
     band bounds alongside the status, so a rejected order can be repriced onto
     a valid tick inside the band rather than merely reported as invalid.
