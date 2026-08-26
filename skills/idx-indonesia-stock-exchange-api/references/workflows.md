# Workflows for IDX Indonesia Stock Exchange Integration

1. **Ticker, Segment & Input Validation**:
   - Verify a 4-letter uppercase ASCII equity code; reject rights (`-R`) and warrants (`-W`).
   - Validate the market segment (`RG`/`TN`/`NG`), the side (`BUY`/`SELL`) and a positive integer share quantity.
   - Require a finite, strictly positive `reference_price` (Acuan Harga). A zero or missing previous close must raise — it is the divisor for the Auto Rejection band and the anchor for the tick size.
2. **Segment Branch**:
   - `RG` / `TN`: continuous JATS order book — apply steps 3-7.
   - `NG`: Pasar Negosiasi — round lot, Fraksi Harga, volume cap and Auto Rejection are all inapplicable. Record them as satisfied with a null Auto Rejection band and skip to step 8.
3. **Fraksi Harga Tick Size Selection**:
   - Select the tick from the **reference price** band, not the order price. IDX fixes it from the previous close for the full trading day.
   - Verify the order price is a whole number of Rupiah and an exact multiple of the tick (integer arithmetic — no float tolerance).
4. **Minimum Price Floor**: Rp 50 on Main/Development/New Economy; Rp 1 on Acceleration/Watchlist.
5. **Board Lot Audit**: quantity must be a multiple of 100 shares.
6. **Order Volume Auto Rejection**: quantity must not exceed `min(50,000 lots, 5% of listed shares)`. Without a listed-share count, enforce the lot cap and record that the 5% leg was not evaluated.
7. **Price Auto Rejection Audit**:
   - Main/Development/New Economy: ARA +35% / +25% / +20% by reference-price band; ARB a flat −15%.
   - Acceleration/Watchlist: ±Rp 1 up to a Rp 10 reference price, else ±10%.
   - Clamp the lower bound to the minimum price floor, then test `lower <= price <= upper`.
8. **Order Execution Logging**:
   - Output a structured `IdxOrderReport` carrying the applied tick, lot count, band edges, per-check booleans, a single status, and an audit note. Log rejections at `WARNING` and acceptances at `INFO`.
