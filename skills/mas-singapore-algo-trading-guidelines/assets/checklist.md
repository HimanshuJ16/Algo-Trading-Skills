# Pre-Flight Checklist — Singapore SGX Algorithmic Order Gate

## Get the jurisdiction right

- [ ] Confirmed the gate does **not** demand a MAS algorithm registration number. MAS issues none, and no Singapore rule requires one.
- [ ] Entity's Capital Markets Services licence or exemption verified against the MAS Financial Institutions Directory — not merely asserted by a config flag.
- [ ] Approved Trader / Registered Representative registration current and recorded on the order (SGX FTR 2.13.2, 2.13.4).
- [ ] `order.algo_id` matches the config being applied, so no order is audited against another algorithm's limits.

## Calibrate the firm-set numbers

- [ ] `max_order_value` calibrated with the Clearing Member and **not** left at the placeholder default. SGX publishes no figure (FTR 3.9.1(3)).
- [ ] `max_order_rate_per_sec` calibrated to the algorithm and gateway throttle.
- [ ] `limit_currency` matches the currency the instrument is priced in.
- [ ] Pre-deployment testing signed off; kill switch armed and reachability tested, not just flagged.

## Get the circuit breaker inputs right

- [ ] `circuit_breaker_ref_price` is the last traded price **at least five minutes earlier** — not the current mid, not the current last done.
- [ ] `is_circuit_breaker_eligible` refreshed **today** (start-of-Market-Day reference price ≥ 0.50 in the underlying currency; JPY 500 for yen-denominated).
- [ ] `session` reflects the real trading phase; the mechanism does not run during the opening and closing routines.
- [ ] For SGX-DT, `circuit_breaker_band_pct` overridden with the **contract's own** price limit (FTR 4.1.15). The 10% default is SGX-ST securities only.
- [ ] `opposite_best_price` supplied wherever available, so marketability is known rather than conservatively assumed.

## Get the Forced Order Range inputs right

- [ ] `min_bid_size` is the instrument's current minimum bid size.
- [ ] `forced_order_range_bids` matches the product class (±30 is the published figure for stocks below a 0.20 bid size, and for ETFs and debentures).
- [ ] Force Key overrides are captured in the audit trail rather than suppressed.

## Verify the gate itself

- [ ] NaN and infinite prices raise rather than being approved.
- [ ] Zero and negative quantities raise rather than being approved.
- [ ] A deviation just past the band (e.g. +10.0049% against a 10% band) rejects — thresholds compared unrounded.
- [ ] A price exactly at the band or exactly at the range boundary passes — both are inclusive.
- [ ] A non-marketable order priced outside the band is approved with a latent-trigger warning, not rejected.
- [ ] Every check runs on every order; all breaches appear in `breaches`, not just the first.
- [ ] Checks that did not run report `None`, never `0.0`.
