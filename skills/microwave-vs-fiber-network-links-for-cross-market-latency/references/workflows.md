# Workflows for Cross-Market Link Arbitration

## 1. Establish the route geometry

1. Get the **route** distance for each link, not the great-circle distance.
   - Fiber: the carrier's route kilometres, and ask whether the figure includes
     slack. Spread Networks' Chicago–NY link is 1,328 km of glass over a 1,253 km
     ground path — ~6% slack — against an 1,176 km geodesic.
   - Microwave: the sum of tower-to-tower hop lengths, plus the fiber tails from
     each data centre to the first and last tower (`fiber_tail_km`, summed across
     both ends).
2. If only a geodesic is available, say so in the report rather than silently
   using it: the resulting fiber budget is understated by roughly 12% on this
   corridor and more on worse-served pairs.
3. Establish which fiber is lit. `propagation_speed_km_s` defaults to G.652
   (204,190 km/s); G.655 NZ-DSF is 203,940 km/s. Ask the carrier — and ask
   whether spools were added for service differentiation, which is a documented
   practice.

## 2. Build the equipment model

1. `repeater_count`: tower count on the radio path, or in-line amplifiers /
   regenerators on the fiber path.
2. `per_repeater_latency_us`: from the radio or amplifier datasheet. **This skill
   supplies no default.** At 22 towers, a 1.4 µs difference per tower is enough to
   invert the ranking between two real Chicago–NJ carriers.
3. `payload_bytes` and `bandwidth_mbps`: serialization is a latency term. A
   1,500-byte frame is 120 µs on a 100 Mbps radio channel and 1.2 µs on 10 Gbps
   fiber. FCC Part 101 coordinates at most 60 MHz at 6 GHz and 80 MHz at 11 GHz,
   so radio channels are structurally narrow.
4. If you leave all of these at zero, the report sets
   `is_microwave_lower_bound_only = True` and stamps `LOWER BOUND` on the audit
   note. Do not strip that warning downstream.

## 3. Compute and interpret the advantage

1. One-way propagation: $d / (c/n) \times 1000$ ms. Add repeaters, fiber tail and
   serialization for the total.
2. Round trip is $2 \times$ one-way **only on a symmetric path**. Microwave-out /
   fiber-back is common; budget the legs separately with
   `co-location-provider-selection-and-network-topology`.
3. Advantage is computed on unrounded values; report fields are rounded
   afterwards. Comparing rounded RTTs would let a marginal difference vanish.
4. Sanity-check the microwave figure against a published measurement for the same
   city pair. A propagation-only model *must* land below it. If yours lands above
   a published measurement, the distance or the speed is wrong.

## 4. Calibrate the degradation thresholds

1. `min_snr_db` = your radio's demodulation threshold for the modulation you run,
   plus the fade margin from the link budget. Derive the rain component with
   ITU-R P.837 (rain rate) → P.838 (specific attenuation) → P.530 (link design).
   The engine does not compute these.
2. `max_telemetry_age_s` = how stale an observation may be and still count as
   evidence. During a squall line, minutes-old weather is not evidence.
3. `max_packet_loss_threshold_pct` (failover) and `recovery_packet_loss_pct`
   (return), with the recovery threshold strictly below the failover threshold.
   The engine rejects equal thresholds: a single shared threshold has no
   hysteresis band and flaps.
4. `recovery_dwell_evaluations`: how many consecutive clean evaluations before
   returning to radio. Match it to your evaluation cadence — three evaluations at
   a 1 s cadence is a 3 s dwell, which is short for a passing storm cell.
5. `known_weather_states` / `degrading_weather_states` if your weather vendor uses
   a different vocabulary. Map onto them or supply your own; never widen the known
   set just to silence a fail-closed verdict.

## 5. Arbitrate, per evaluation cycle

1. Collect telemetry for **both** links. Fiber telemetry is optional but a
   failover into a dead backup is otherwise invisible.
2. Call `arbitrate_cross_market_links(...)`, carrying `previous_status` and
   `consecutive_clean_evaluations` forward from the last verdict. The engine is
   stateless by design; the state lives in your control loop.
3. Act on the status:

   | Status | Action |
   |---|---|
   | `ROUTE_MICROWAVE_PRIMARY` | Route over radio. |
   | `FAILOVER_TO_FIBER_RAIN_FADE` | Route over fiber. Alert; a measured degradation fired. |
   | `FAILOVER_TO_FIBER_TELEMETRY_UNUSABLE` | Route over fiber **and page**: this is a monitoring failure, not a weather event. Fix the feed. |
   | `HOLD_FIBER_RECOVERY_HYSTERESIS` | Stay on fiber. Nothing is wrong; the dwell has not elapsed. |
   | `NO_HEALTHY_LINK_ESCALATE` | Nominate no route. Halt the cross-market strategy and escalate. |

4. Never re-derive a route from `microwave_rtt_ms` alone. The latency fields
   describe the corridor; `status` and `selected_routing_link_id` describe the
   decision.

## 6. Sizing and post-trade review

1. Size the strategy so it clears costs at **fiber** latency. Published
   trading-hour availability for a microwave corridor has been reported at ~99%
   against fiber's claimed 99.999%, and the microwave outage window correlates
   with weather-driven volatility rather than being randomly distributed.
2. Track realised time-in-status. If `FAILOVER_*` plus `HOLD_*` materially exceeds
   your assumption, the economics were modelled on the wrong link.
3. Track `FAILOVER_TO_FIBER_TELEMETRY_UNUSABLE` separately from rain fade. A
   rising count is a broken telemetry pipeline that a weather-only dashboard will
   report as good weather.
4. Consider path redundancy, not just headline latency. Alternate-path
   availability differs across carriers by more than 2× on this corridor, and the
   slower-on-paper network can be the faster one in bad weather.
