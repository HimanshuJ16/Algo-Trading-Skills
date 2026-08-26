# Pre-Flight Checklist

## Budget sourcing

- [ ] Is `exchange_max_mps` taken from the venue's own documentation for **this** session, rather than the library default?
- [ ] If the venue publishes both a reject and a terminate threshold, is the **reject** threshold configured?
- [ ] Is the venue's measurement window known (instantaneous vs rolling average), and is burst behaviour covered by a separate control?
- [ ] Is `target_safety_buffer_pct` within $(0, 100]$, with the chosen headroom justified and recorded?

## Inputs

- [ ] Does `active_quoting_pairs` count **messages** per reprice cycle (double it if the strategy sends cancel + new rather than a cancel/replace)?
- [ ] Is co-resident session flow (hedges, cancels, other strategies) passed as `baseline_session_mps`? (MiFID II RTS 6 Art. 15(2) counts all orders sent to the venue.)
- [ ] Is `price_volatility_bps` the current regime's volatility, not a calm-market default?

## Output gate

- [ ] Is `is_target_achievable` checked **before** deploying — not just `status != DIRECT_PASS`?
- [ ] For an achievable report, does `projected_message_rate_mps <= target_safety_limit_mps` hold?
- [ ] Is `RATE_LIMIT_TARGET_UNREACHABLE` wired to an alert, and blocked from auto-deployment?
- [ ] Is the adverse selection exposure score logged for regime comparison (and not treated as a currency cost)?

## Enforcement

- [ ] Is the recommended delay actually **enforced** by a runtime throttle in the order path? (A recommendation is not the RTS 6 Art. 15(1)(d) pre-trade control.)
- [ ] Is the tuner re-run when volatility, tick rate, quoting pairs, or session composition changes?
- [ ] Is order-to-trade / message-efficiency exposure tracked separately from the MPS ceiling?
