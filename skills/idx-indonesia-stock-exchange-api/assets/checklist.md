# Pre-Flight Checklist

- [ ] Is the IDX ticker format verified (4 uppercase ASCII letters, no `-R`/`-W` suffix)?
- [ ] Is the market segment specified (`RG`, `TN`, `NG`) and the side one of `BUY`/`SELL`?
- [ ] Is `reference_price` the **previous closing price** (or post-corporate-action theoretical price / debut price) — not the live or order price?
- [ ] Is `reference_price` validated as finite and strictly positive before it is used as a divisor?
- [ ] Is the tick size selected from the reference price band, and held fixed for the whole trading day?
- [ ] Is the order price a whole number of Rupiah and an exact multiple of that tick?
- [ ] Is the price at or above the minimum floor (Rp 50 ordinary boards / Rp 1 Acceleration-Watchlist)?
- [ ] Are order quantities verified as 100-share Board Lot multiples on `RG` and `TN`?
- [ ] Is the order within the per-order volume cap: `min(50,000 lots, 5% of listed shares)`?
- [ ] Is the Auto Rejection band applied **asymmetrically** (ARA 35/25/20% by band, ARB flat 15%)?
- [ ] Is the correct listing board used (`MAIN`/`DEVELOPMENT`/`NEW_ECONOMY` vs `ACCELERATION`/`WATCHLIST`)?
- [ ] Are `NG` (Pasar Negosiasi) orders exempted from round lot, tick, volume cap and Auto Rejection?
- [ ] Have the tick/ARA/ARB schedules been re-verified against IDX for the current effective date?
