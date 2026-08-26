# Pre-Flight Checklist — Cross-Market Link Arbitration

## Route geometry
- [ ] Fiber `distance_km` is the carrier's **route/glass** length, not a great-circle distance (the Chicago–NJ gap is ~12%).
- [ ] Confirmed with the carrier whether the quoted length includes slack coils (~6% on the reference route).
- [ ] Microwave `distance_km` is the sum of tower-to-tower hops.
- [ ] `fiber_tail_km` is set for the data-centre-to-tower fiber at **both** ends — the radio path is hybrid.
- [ ] `propagation_speed_km_s` matches the fiber type actually lit (G.652 204,190 / G.655 203,940 / ULL 205,056 km/s).

## Equipment model
- [ ] `repeater_count` reflects the real tower or amplifier count on the path.
- [ ] `per_repeater_latency_us` comes from the vendor datasheet — not from this skill, which supplies no default.
- [ ] `bandwidth_mbps` is the real channel rate, and `payload_bytes` is set if serialization matters at your message size.
- [ ] If any of the above is left at zero, the `LOWER BOUND` warning is preserved everywhere the figure is reported.
- [ ] The modelled microwave one-way figure has been sanity-checked against a published measurement for the same city pair and lands *above* the propagation floor.

## Thresholds
- [ ] `min_snr_db` set from the radio's demodulation threshold plus link-budget fade margin (derived via ITU-R P.837 → P.838 → P.530).
- [ ] `max_telemetry_age_s` set — leaving it `None` disables the staleness check entirely.
- [ ] `recovery_packet_loss_pct` is strictly below `max_packet_loss_threshold_pct`.
- [ ] `recovery_dwell_evaluations` × evaluation cadence is long enough to outlast a passing storm cell.
- [ ] Weather vocabulary matches the vendor feed, and the known set was **not** widened just to silence a fail-closed verdict.

## Telemetry and failover
- [ ] Telemetry is collected for the fiber backup, not only the microwave primary.
- [ ] `NO_HEALTHY_LINK_ESCALATE` is wired to halt the cross-market strategy, not to pick the least-bad degraded path.
- [ ] `FAILOVER_TO_FIBER_TELEMETRY_UNUSABLE` pages someone — it is a monitoring failure, not weather.
- [ ] `previous_status` and `consecutive_clean_evaluations` are carried forward between evaluations; the engine holds no state.
- [ ] Route-flap rate has been measured under a replay of real degraded telemetry, not just clean-weather data.

## Strategy economics
- [ ] The strategy clears its costs at **fiber** latency, not only at microwave latency.
- [ ] Sizing assumes microwave availability in the region of two nines during trading hours, not five.
- [ ] The outage window is treated as weather-correlated — i.e. concentrated in volatile sessions — rather than uniformly distributed.
- [ ] Realised time-in-status is tracked post-trade against the modelled assumption.
- [ ] Carrier selection weighed alternate-path availability, hop length and operating band, not headline latency alone.

## Regulatory
- [ ] Fixed point-to-point links are licensed (47 CFR Part 101 in the US) and frequency-coordinated.
- [ ] Interference risk — distinct from weather — is covered by the SNR and packet-loss signals, not by the weather state alone.
