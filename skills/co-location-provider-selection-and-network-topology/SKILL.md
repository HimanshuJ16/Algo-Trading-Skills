---
name: co-location-provider-selection-and-network-topology
description: >-
  Use when choosing where to place trading servers across co-location facilities
  and cross-venue fiber/microwave links: decomposes one-way and round-trip latency
  budgets from first-principles propagation physics, computes monthly TCO, and
  ranks candidate facilities with a weighted latency/cost score.
domain: Infrastructure
subdomain: Network Architecture
tags:
- colocation
- latency-budget
- equinix-ny4
- cme-aurora
- slough-ld4
- network-topology
- propagation-delay
- facility-scoring
brokers_frameworks:
- Generic Infrastructure
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deciding **where to put the box** for latency-sensitive or cross-venue arbitrage strategies. Placing an equities-futures arbitrage engine in Equinix NY4 (Secaucus) instead of CME's Aurora IL data centre adds thousands of microseconds of propagation delay that no amount of code optimisation recovers. The evaluator turns a candidate list into a defensible comparison: it decomposes each path into propagation, switching and cross-connect delay, computes monthly total cost of ownership (TCO), and produces a ranked, weighted latency-versus-cost score.

## When NOT to Use

- **As a substitute for a measured latency figure.** This is a *procurement-stage* model. It computes what physics and published equipment specs permit; it does not model queueing delay, congestion, microburst buffering, serialisation delay, kernel/NIC overhead, or the carrier's actual (usually undisclosed) route. Once a circuit is live, measure it — see `tick-to-trade-latency-measurement` and `colocation-latency-budget-accounting`.
- **For tick-to-trade / in-host budgeting.** This skill stops at the cage door. Everything from NIC ingress to NIC egress belongs to `colocation-latency-budget-accounting` and `strategy-latency-budget-decomposition`.
- **For link-availability decisions.** Ranking by *mean* latency says nothing about jitter or rain fade. A microwave link that is 2 ms faster and unavailable during thunderstorms is an availability decision, not a latency one: see `microwave-vs-fiber-network-links-for-cross-market-latency` and `network-jitter-impact-on-strategy-performance`.
- **When the candidate set is one facility.** Scores are min-max normalised *within* the supplied candidate set, so a single candidate always scores 0.0. The score is a relative ranking, never an absolute grade.

## Prerequisites

- **Route** distance in km between each candidate facility and the target matching engine — the physical cable or line-of-sight path, not the great-circle distance. If only a straight-line distance is known, supply it and set `route_circuity_factor` (fiber rights-of-way typically run 1.15-1.30x straight line).
- Link medium per path (`FIBER` or `MICROWAVE`), switch hop count, and the port-to-port latency of the switches actually deployed.
- Monthly commercial terms per facility: rack MRC, committed kW, $/kW, and cross-connect MRC.

## Units

- All latency values are **microseconds (µs)**; propagation constants are **µs/km**. Distances are **km**. Costs are **monthly recurring** in a single currency — the model does no FX conversion.
- `composite_score` is a **penalty in [0, 1]: lower is better.** It is not a quality percentage.

## Workflow

1. **Topology Definition**: Define candidate `FacilitySpec` nodes (e.g. `NY4`, `CARTERET`, `AURORA`, `LD4`) and the `NetworkLinkSpec` paths from each to its target matching engine. `location_code` must be unique per facility — the evaluator raises rather than guess which facility a link belongs to.
2. **Choose the Propagation Constant Deliberately**:
   - Fiber defaults to the conservative planning convention of $5.0\,\mu\text{s/km}$ (200 km/ms). SMF-28 physics is $4.897\,\mu\text{s/km}$; the default is deliberately *slower* so the model never promises a latency the medium cannot deliver.
   - Microwave uses $c/n_{\text{air}} = 3.3366\,\mu\text{s/km}$. Do **not** substitute the widespread "3.33 µs/km (300 km/ms)" shorthand — 300,000 km/s exceeds $c$ and is not achievable.
   - If the carrier publishes a *measured* one-way figure for the circuit, set `propagation_us_per_km` and use it. Any override faster than light in vacuum is rejected.
3. **Latency Budget Decomposition** (`calculate_latency_budget`):
   - $\text{Route Distance} = \text{Distance (km)} \times \text{Circuity Factor}$
   - $\text{Propagation} = \text{Route Distance} \times \text{Propagation Constant}$
   - $\text{Switching} = \text{Num Switches} \times \text{Switch Latency}$
   - $\text{Total One-Way} = \text{Propagation} + \text{Switching} + \text{Cross-Connect}$
   - `total_rtt_us` doubles the one-way figure and is therefore **only valid for a symmetric path**. If the return leg uses a different medium — the common cross-market pattern of microwave out, fiber back — call `calculate_round_trip_budget(outbound, inbound)` instead.
4. **TCO Calculation** (`calculate_facility_tco`): $\text{TCO} = \text{Rack MRC} + (\text{kW} \times \text{Rate}) + \text{Cross-Connect MRC}$.
5. **Multi-Attribute Scoring** (`evaluate_colocation_setup`): latency and TCO are min-max normalised across the rankable candidates and combined as $\text{Score} = w_{lat} \times \text{NormLatency} + w_{cost} \times \text{NormCost}$. Weights are rescaled to sum to 1, so `(7, 3)` and `(0.7, 0.3)` are identical.
6. **Read the Unranked Tail**: a facility with no modelled link is returned with `rank=None` and `avg_rtt_us=None`, after the ranked entries, and a warning is logged. It is **not** a zero-latency winner — it is missing data. Model its links, then re-run.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming Vacuum Speed in Glass**: Light in silica fiber travels at $c/n_g$ with $n_g \approx 1.4682$ (Corning SMF-28 at 1550 nm), i.e. $\approx 4.9\,\mu\text{s/km}$ — roughly 47% slower than the $3.34\,\mu\text{s/km}$ vacuum figure. Over the ~1,200 km Chicago-New Jersey corridor that is a ~1.9 ms one-way error.
- **Quoting Microwave at 300,000 km/s**: The "3.33 µs/km" shorthand is faster than light in vacuum. It flatters microwave by ~4 µs on a 1,200 km corridor and, worse, normalises modelling numbers that no physical link can achieve.
- **Feeding Great-Circle Distance into a Route Model**: Fiber follows highway and rail rights-of-way; microwave follows a chain of tower sites. Using a map's straight-line distance systematically understates every fiber budget by 15-30%. Set `route_circuity_factor`, or obtain the carrier's route km.
- **Doubling a One-Way Figure Across an Asymmetric Path**: Cross-market topologies routinely run microwave in one direction and fiber in the other. `total_rtt_us = 2 x one_way` is wrong by the full medium delta in that case.
- **Using a Generic Switch Latency**: A Layer-1 switch (Arista 7130: 4 ns port-to-port) and an ultra-low-latency L2/L3 switch (Arista 7150: 350 ns) differ by ~90x per hop. Over four hops that is 1.4 µs — larger than the entire propagation delay of an in-building cross-connect. Use the spec sheet for the hardware actually racked.
- **Treating a Missing Link as Zero Latency**: Any scoring model that silently substitutes 0 for "not measured" ranks the least-understood facility first. Missing latency data must stay `None` and stay unranked.
- **Comparing Scores Across Runs**: Normalisation is relative to the candidate set. Adding or removing a candidate changes every other candidate's score. Only compare scores computed in the same call.
- **Ignoring Equal-Length Patching**: Failing to request matched-length fiber cross-connects inside a shared cage leaves microsecond-scale skew between servers that the topology model — which sees only the ordered lengths — will never show you.

## Verification

- **Propagation constants**: $1/c = 3.335641\,\mu\text{s/km}$ (vacuum floor); microwave $= 3.336642\,\mu\text{s/km}$, which equals the published $\approx 5.37\,\mu\text{s}$ per statute mile; fiber physics $= 3.335641 \times 1.4682 = 4.897388\,\mu\text{s/km}$.
- **Corridor check**: model the ~1,200 km Aurora-Secaucus corridor. Fiber at the 5.0 µs/km convention gives $6{,}000\,\mu\text{s}$ (6.00 ms) one-way; microwave gives $4{,}003.97\,\mu\text{s}$ (4.00 ms). The microwave figure brackets the ~4.1 ms one-way best case widely reported for that corridor's microwave networks — a secondary-source figure this skill has not independently verified, and one that sits *above* the model output precisely because real routes add tower hops and radio latency the propagation term alone omits.
- **Scoring check**: three facilities with RTTs of 100/550/1000 µs and TCOs of \$4,000/\$6,000/\$3,000, scored at $w_{lat}=0.7$, $w_{cost}=0.3$, must produce penalty scores of 0.10, 0.65 and 0.70 and ranks 1, 2, 3 respectively. Flipping to $w_{cost}=1.0$ must reorder them by cost alone.
- Run `python -m unittest discover -s skills/co-location-provider-selection-and-network-topology/scripts`.

## Related Skills

- `microwave-vs-fiber-network-links-for-cross-market-latency`
- `cross-venue-latency-arbitrage-defensive-design`
- `colocation-latency-budget-accounting`
- `strategy-latency-budget-decomposition`
- `network-jitter-impact-on-strategy-performance`
