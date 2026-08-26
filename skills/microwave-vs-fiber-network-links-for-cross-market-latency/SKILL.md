---
name: microwave-vs-fiber-network-links-for-cross-market-latency
description: >-
  Cross-market microwave-vs-fiber link arbitration: decomposes corridor latency into propagation, repeater and serialization terms instead of quoting a propagation-only floor as an estimate, and resolves the speed-versus-availability trade-off with fail-closed rain-fade failover and recovery hysteresis.
domain: Market Microstructure Latency
subdomain: Cross-Market Latency & Wireless Infrastructure Optimization
tags: ["microwave-link", "fiber-optic", "cross-market-latency", "propagation-speed", "rain-fade", "chicago-to-nj", "line-of-sight", "hft-infrastructure"]
brokers_frameworks: ["CME Aurora", "NJ Secaucus / Carteret Data Centers", "ITU-R P.453 / P.530", "FCC Part 101", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deciding whether a cross-market strategy should ride a licensed microwave path or a fiber path — the Chicago CME Aurora to New Jersey (Secaucus NY4 / Carteret / Mahwah) corridor being the canonical case — and when arbitrating between them in real time as weather degrades the radio link.

The headline is real: radio travels the corridor at essentially $c$, fiber at roughly $2c/3$ over a longer path, and best-in-class published figures are ~8.2 ms round trip on microwave against ~12.98 ms on the fastest purpose-built fiber. But two things routinely turn that headline into a wrong decision:

1. **A propagation-only model is a floor, not an estimate.** A radio corridor is a chain of repeater towers — 22 on the shortest reconstructed CME–NY4 path — and the last hop into each data centre is fiber, not air. Bhattacherjee et al. rank five competing Chicago–NJ networks within **0.4–8.1 µs** of each other and note that if per-tower added latency exceeded **1.4 µs**, the ranking between two of them inverts. The equipment term this skill's previous version dropped is larger than the margin the whole exercise is trying to resolve.
2. **The trade-off is availability, not speed.** McKay Brothers' co-founder said their microwave network "was down 1 per cent of the time during trading hours in December and January," against Spread Networks' claimed **99.999%** fiber availability. Two nines against five. A strategy that only clears its costs at 8 ms is adversely selected during precisely the ~1% of trading time when everyone fast is on fiber.

The engine decomposes each link into propagation, repeater and serialization terms, labels a figure that omits the equipment terms as a lower bound rather than presenting it as the link's latency, and returns a routing verdict that fails *closed* — unknown weather states, stale telemetry and a simultaneously-degraded backup each produce an explicit status instead of defaulting onto the fragile link.

## When NOT to Use

- **As a latency measurement tool.** This module reads no clock and probes nothing. It is arithmetic over a link configuration you supplied. If you want the corridor's *actual* latency, measure it — see `tick-to-trade-latency-measurement` and `latency-monitoring-percentile-based-slas`.
- **For the in-host or facility latency budget.** Rack-to-rack and NIC-to-NIC budgets belong to `colocation-latency-budget-accounting`; multi-facility siting and TCO ranking to `co-location-provider-selection-and-network-topology`. This skill owns only the inter-market link choice.
- **On an asymmetric path.** `*_rtt_ms` is `2 × one_way` and is therefore valid only when both directions use the same medium. The common cross-market topology is microwave out and fiber back; budget each leg separately.
- **With the shipped equipment terms left at zero.** `repeater_count`, `per_repeater_latency_us` and `fiber_tail_km` default to zero and this skill supplies **no** default per-tower latency — that figure comes from your radio vendor's datasheet, and inventing one would be worse than omitting it. Left at zero, the report is a floor and says so.
- **As a spectrum, licensing or link-budget design tool.** Fixed point-to-point microwave in the US is licensed under 47 CFR Part 101 and requires frequency coordination. Rain attenuation is predicted with ITU-R P.838 (specific attenuation), P.837 (rain rate statistics) and P.530 (link design). This module implements none of them: it consumes a fade/SNR threshold you derived from them.
- **As a real-time circuit breaker.** This is a stateless evaluator, not a control loop. Halting trading on a link outage belongs in `kill-switch-and-drawdown-circuit-breakers`.

## Prerequisites

- **Route distances, not great-circle distances.** For CME Aurora to Nasdaq Carteret the geodesic is ~1,176 km; Spread Networks' fiber is 1,328 km of glass, whose ground path Bozkurt et al. measured at 1,253 km with the remaining ~6% attributed to slack coils left for future repairs. Feeding a geodesic into a fiber budget understates it by roughly that whole 12%.
- **The fiber type actually lit on the route.** `propagation_speed_km_s` defaults to 204,190 km/s (Corning SMF-28 group index 1.4682 at 1550 nm, ITU-T G.652). G.655 NZ-DSF runs at 203,940 km/s and ultra-low-latency fiber at 205,056 km/s. Over 1,328 km that choice alone is **~8 µs one-way** — larger than the margin separating competing radio networks.
- **The equipment terms**: `repeater_count` (towers, or in-line amplifiers on fiber), `per_repeater_latency_us` from the radio/amplifier datasheet, and `fiber_tail_km` for the data-centre-to-tower fiber at both ends.
- **Link telemetry** per link: `current_weather` from a vocabulary the engine knows, `packet_loss_pct`, `signal_to_noise_ratio_db`, and ideally `telemetry_age_s`.
- **Thresholds you calibrated**: `min_snr_db` is your radio's modulation-specific demodulation floor plus its fade margin; `max_telemetry_age_s` is how stale an observation may be before it stops being evidence. Both default to `None`, which disables the check — set them.
- **Fiber telemetry.** Optional, and the single most valuable optional input: without it, failover into a dead backup is indistinguishable from a successful one.

## Workflow

1. **Decompose the latency, and refuse to launder a floor as an estimate.**
   - One-way propagation is $\tau = \frac{d_{\text{km}}}{c/n} \times 1000$ ms, with $n = 1.000315$ for radio (ITU-R P.453 average sea-level refractivity $N_0 = 315$) and $n = 1.4682$ for fiber. Round trip is $2\tau$ **only on a symmetric path**.
   - Add the equipment terms: `repeater_count × per_repeater_latency_us`, the fiber tail at fiber speed, and serialization if a payload size is given.
   - If no equipment term was supplied, `is_microwave_lower_bound_only` is `True` and the audit note says `LOWER BOUND`. Treat that report as an upper bound on the *advantage*, never as the link's latency.
2. **Compute the advantage on unrounded values.** $\Delta = \text{RTT}_{\text{fiber}} - \text{RTT}_{\text{mw}}$ and $\text{Adv}_\% = \Delta / \text{RTT}_{\text{fiber}} \times 100$. Rounding is applied to the report fields only. Defaulting fiber to the *fastest* common type (G.652) is deliberate: it understates the microwave advantage rather than flattering it.
3. **Classify the telemetry before believing it.** A weather string outside `known_weather_states`, or an observation older than `max_telemetry_age_s`, is *unusable* — not *fine*. Both fail closed to fiber. This is the difference between a vendor renaming an enum and a silent month of routing over an unassessed link.
4. **Test the degradation signals, in the order they lead.** SNR collapses first, the radio drops to a more robust modulation, and only then do packets disappear. Weather state and packet loss are lagging confirmations; `min_snr_db` is what catches a fade before it costs fills.
5. **Resolve the status by precedence**, and treat a lost backup as its own outcome:
   `NO_HEALTHY_LINK_ESCALATE` > `FAILOVER_TO_FIBER_RAIN_FADE` > `FAILOVER_TO_FIBER_TELEMETRY_UNUSABLE` > `HOLD_FIBER_RECOVERY_HYSTERESIS` > `ROUTE_MICROWAVE_PRIMARY`.
   When both links are degraded the engine nominates **no** route and returns `selected_link_type = "NONE"`. Stop the cross-market strategy; do not let it pick the least-bad degraded path on its own.
6. **Return to radio on hysteresis, never on a single clean reading.** Failover trips above `max_packet_loss_threshold_pct` (default 1.0%); recovery requires loss at or below `recovery_packet_loss_pct` (default 0.1%) for `recovery_dwell_evaluations` consecutive evaluations (default 3). A single shared threshold flaps: loss oscillating either side of 1.0% swaps the route on every evaluation, and each swap reorders packets in flight on a path whose entire competitive margin is single-digit microseconds. The engine is stateless — carry `previous_status` and `consecutive_clean_evaluations` forward yourself.
7. **Read the frequency note as a design signal.** ITU-R P.530: rain attenuation "can be ignored at frequencies below about 5 GHz, but must be included in design calculations at higher frequencies, where its importance increases rapidly." A 6 GHz network and an 11 GHz network on the same corridor are not the same product.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Quoting a propagation-only figure as the link's latency.** It is a floor no physical link achieves. The published measured figure for Aurora→Carteret was 3.982 ms one-way in 2016; propagation over the 1,176 km geodesic is 3.924 ms. The ~58 µs gap is towers, radios and fiber tails — and it is roughly ten times the 0.4–8.1 µs that separates competing networks on that corridor. A model that drops it cannot rank two carriers.
- **Assuming an unrecognised weather string means good weather.** A vendor enum change, a typo, or an empty field previously fell through to `ROUTE_MICROWAVE_PRIMARY`. Nothing errors, nothing alerts, and live orders route over a link whose condition was never assessed. Unknown state must fail closed.
- **Failing over to a backup you never checked.** If the fiber path is also degraded, a "successful" failover is indistinguishable from a working one in every log line it produces. Supply `fiber_telemetry` and handle `NO_HEALTHY_LINK_ESCALATE`.
- **Using one packet-loss threshold for both directions.** Loss hovering around 1.0% flaps the route on every evaluation. Mid-flight route changes reorder packets across paths 5 ms apart in latency — worse for a sequenced feed than staying on the slow path.
- **Sizing the strategy on microwave latency alone.** At ~99% trading-hour availability, the fiber path carries you for on the order of 1% of the session — and it is not a random 1%, it is the volatile weather-driven 1%. If the edge does not survive at fiber latency, it does not survive.
- **Assuming the "microwave" path is all air.** Data centres are not on towers. The corridor is hybrid, with fiber tails at both ends that run at $2c/3$; leaving `fiber_tail_km` at zero understates the radio path by tens of microseconds.
- **Ignoring which fiber is lit.** G.652 versus G.655 NZ-DSF is ~8 µs one-way over this corridor. Worse, Bozkurt et al. record that "in some instances, fiber providers deliberately increase the amount of fiber in a link (through the addition of fiber spools) for the purpose of service differentiation" — your circuit may be slowed on purpose relative to the premium tier.
- **Feeding a great-circle distance into a fiber budget.** Spread Networks' corridor is 1,328 km of glass against an 1,176 km geodesic: +12%, and that is the *shortest* route ever built for the pair.
- **Forgetting that bandwidth is a latency term.** FCC Part 101 coordinates up to 60 MHz at 6 GHz and 80 MHz at 11 GHz — narrow channels. A single 1,500-byte frame costs 120 µs of serialization on a 100 Mbps radio link against 1.2 µs on 10 Gbps fiber. This is why radio corridors carry a curated subset and the bulk feed stays on fiber.
- **Letting a NaN into a distance or a loss figure.** Every comparison against NaN is `False`, so a NaN distance yields a NaN RTT that never breaches anything and renders as a clean primary route. The engine rejects it instead.

## Verification

- Constants derived, not rounded: $n_{\text{air}} = 1.000315 \implies 299{,}698.05$ km/s (3.336692 µs/km); $n_{\text{group}} = 1.4682 \implies 204{,}190.48$ km/s (4.897388 µs/km). No medium may be configured faster than $c$ — `propagation_speed_km_s = 300000` is rejected as superluminal.
- Corridor arithmetic, hand-derived: 1,176 km of air $\implies$ 3.9239494 ms one-way, 7.8478988 ms RTT; 1,328 km of G.652 fiber $\implies$ 6.5037313 ms one-way, 13.0074626 ms RTT $\implies$ saving 5.159564 ms, advantage **39.67%**.
- Lower-bound discipline: propagation-only microwave (3.9239 ms) lands *below* the published measured 3.982 ms one-way; adding 22 towers at 2.5 µs and a 20 km fiber tail moves the round-trip saving to 4.853668 ms (**37.31%**) — the equipment term costs **306 µs** of the headline advantage, and clears the `LOWER BOUND` warning from the audit note.
- Fiber type: G.652 versus G.655 NZ-DSF over 1,328 km $\implies$ 7.97 µs one-way.
- Serialization: 1,500 bytes $\implies$ 120.0 µs at 100 Mbps, 12.0 µs at 1 Gbps.
- Fail-closed: `'HEAVYRAIN'`, `''`, and telemetry aged 600 s against a 30 s bound each $\implies$ `FAILOVER_TO_FIBER_TELEMETRY_UNUSABLE`, never `ROUTE_MICROWAVE_PRIMARY`.
- Boundary: exactly 1.0% loss $\implies$ `ROUTE_MICROWAVE_PRIMARY`; 1.0000001% $\implies$ `FAILOVER_TO_FIBER_RAIN_FADE`, with the reason string rendered in significant digits so a marginal breach does not read as `1.000% > 1.000%`.
- Hysteresis: from a failover state, four consecutive clean evaluations at 0.02% loss $\implies$ `HOLD`, `HOLD`, `PRIMARY`, `PRIMARY`; loss of 0.5% (inside the band) $\implies$ `HOLD_FIBER_RECOVERY_HYSTERESIS` with the dwell counter reset to 0.
- Both links degraded $\implies$ `NO_HEALTHY_LINK_ESCALATE` with `selected_link_type = "NONE"` and an empty link id.
- Argument safety: mismatched `telemetry.link_id`, a fiber config passed as the microwave leg, NaN/Inf/negative/out-of-range inputs, and a recovery threshold not strictly below the failover threshold each raise `NetworkLinkError` (a `ValueError` subclass).
- Run `python -m unittest discover -s skills/microwave-vs-fiber-network-links-for-cross-market-latency/scripts`.

## Related Skills

- `co-location-provider-selection-and-network-topology`
- `colocation-latency-budget-accounting`
- `tick-to-trade-latency-measurement`
- `latency-monitoring-percentile-based-slas`
- `network-jitter-impact-on-strategy-performance`
- `cross-venue-latency-arbitrage-defensive-design`
- `multi-region-failover-for-broker-connectivity`
- `kill-switch-and-drawdown-circuit-breakers`
