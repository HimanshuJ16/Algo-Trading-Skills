# Workflows for Co-Location & Topology Evaluation

## 1. Facility Survey

Collect, per candidate facility: location, exchange matching engines reachable
in-building, cross-connect types available and their lead time, committed power
(kW) and $/kW, rack MRC, and cross-connect MRC. Assign each facility a **unique**
`location_code` — the evaluator refuses duplicate codes because a link endpoint
would otherwise be ambiguous.

## 2. Route Distance, Not Map Distance

For every facility-to-matching-engine path, obtain the **route** distance:

- Ask the carrier for route km on the specific circuit being quoted. This is the
  only authoritative number.
- If the carrier will not disclose it, enter the great-circle distance and set
  `route_circuity_factor` (fiber rights-of-way typically 1.15-1.30).
- For microwave, the route is a chain of tower sites; charge the per-hop radio
  latency through `num_switches` / `switch_latency_us` rather than pretending
  the path is a single span.

## 3. Latency Budget Calculation

```
route_km       = distance_km x route_circuity_factor
propagation_us = route_km x propagation_constant
switch_us      = num_switches x switch_latency_us
one_way_us     = propagation_us + switch_us + cross_connect_us
```

Propagation constants (see `standards.md` for derivations):

| Medium | µs/km |
|---|---|
| Fiber (planning default) | 5.000 |
| Fiber (SMF-28 physics) | 4.897 |
| Microwave (free-space RF) | 3.336642 |
| Vacuum floor — nothing may be faster | 3.335641 |

**Round trip.** `total_rtt_us = 2 x one_way_us` holds only when the return leg
uses the same medium over the same route. For an asymmetric topology — the
common microwave-out / fiber-back cross-market pattern — call
`calculate_round_trip_budget(outbound, inbound)` and sum the two legs explicitly.

## 4. TCO Modelling

```
tco_mrc = rack_cost_mrc + (power_kw x power_cost_per_kw) + cross_connect_mrc
```

All figures are monthly recurring, single currency, no FX conversion. Non-recurring
charges (install, remote hands, hardware capex) are out of scope — carry them
separately and be consistent across candidates.

## 5. Scoring & Ranking

```
NormLatency = (avg_rtt_us - min_rtt) / (max_rtt - min_rtt)
NormCost    = (tco_mrc   - min_tco) / (max_tco - min_tco)
Score       = w_lat x NormLatency + w_cost x NormCost      # penalty: lower is better
```

- Weights are rescaled to sum to 1.0; `(7, 3)` and `(0.7, 0.3)` are equivalent.
- Normalisation runs over the **rankable** candidates only. A degenerate spread
  (all candidates identical on an axis) contributes 0.0 on that axis rather than
  dividing by zero.
- Equal scores share a rank (competition ranking: 1, 1, 3). Ordering within a tie
  is by facility name, so results are deterministic regardless of input order.

## 6. Reading the Output

`evaluate_colocation_setup` returns `FacilityScore` records in rank order, with
unrankable facilities appended.

| Field | Meaning |
|---|---|
| `avg_rtt_us` | Mean RTT across every modelled link touching this facility; `None` if no link was modelled. |
| `num_links` | How many links were attributed. A rank built on one link is a weaker signal than one built on five. |
| `normalized_latency` / `normalized_cost` | Position on each axis within the candidate set, 0 = best. |
| `composite_score` | Weighted penalty, lower is better. |
| `rank` | 1 = best. `None` means unranked for lack of latency data. |

Treat `rank=None` as **missing data, not a good result**: model the facility's
links and re-run. A warning is logged whenever this happens.

## 7. Sanity Checks Before Committing

- Recompute one budget by hand; the arithmetic is simple enough that a
  transposed digit in a distance is the most likely error, and the model will
  not catch it.
- Confirm the ranking is stable under a plausible re-weighting (e.g. 0.7/0.3 vs
  0.5/0.5). A ranking that flips on a small weight change is not a decision the
  model is entitled to make for you.
- Confirm the top candidate's advantage exceeds the model's own uncertainty. If
  two facilities differ by 20 µs on a budget whose route distance is a ±15%
  estimate, the model cannot separate them — decide on jitter, availability,
  cross-connect lead time, or cost instead.
