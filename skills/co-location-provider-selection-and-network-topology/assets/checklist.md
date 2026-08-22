# Pre-Flight Checklist

## Distance inputs

- [ ] Is every `distance_km` a **route** distance (cable / tower-chain path), not a great-circle distance?
- [ ] Where only straight-line distance was available, is `route_circuity_factor` set (fiber typically 1.15-1.30)?
- [ ] Has the carrier been asked for the route km of the specific circuit being quoted?

## Propagation constants

- [ ] Is fiber modelled at $5.0\ \mu\text{s/km}$ (conservative planning default) or at a carrier-measured figure — and is the choice deliberate?
- [ ] Is microwave modelled at $3.336642\ \mu\text{s/km}$ ($c/n_{\text{air}}$), **not** the superluminal "3.33 µs/km / 300 km/ms" shorthand?
- [ ] Is every `propagation_us_per_km` override slower than the vacuum floor of $3.335641\ \mu\text{s/km}$?

## Latency budget

- [ ] Does `switch_latency_us` come from the data sheet of the switch actually racked (Layer 1 ~4 ns vs L2/L3 ~350 ns differ by ~90x per hop)?
- [ ] Is in-building patch delay entered as `cross_connect_us` rather than folded into propagation?
- [ ] For any path whose return leg uses a different medium, is `calculate_round_trip_budget(outbound, inbound)` used instead of doubling one leg?
- [ ] Has one budget been recomputed by hand to catch a transposed distance?

## Cost model

- [ ] Does TCO account for committed power density (kW/rack) and cross-connect MRC, not just rack MRC?
- [ ] Are non-recurring charges (install, remote hands, hardware capex) tracked separately and consistently across candidates?
- [ ] Are all candidates quoted in the same currency? The model performs no FX conversion.

## Scoring

- [ ] Is every `location_code` unique across the candidate set?
- [ ] Are the latency and cost weights a deliberate business choice, and is the ranking stable under a plausible re-weighting?
- [ ] Has every `rank=None` entry been treated as **missing latency data**, not as a strong candidate?
- [ ] Is the top candidate's margin larger than the model's own input uncertainty?
- [ ] Are scores being compared only within a single evaluation call (normalisation is relative to the candidate set)?

## Physical operations

- [ ] Have matched-length fiber cross-connects been ordered for all servers in the same cage, to avoid intra-rack skew?
- [ ] Is the latency-versus-availability trade-off (microwave rain fade, link jitter) being decided separately from this mean-latency ranking?
