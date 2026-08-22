# Standards for Co-Location Evaluation

## Propagation constants

Every constant below is **derived**, not asserted. The base quantity is the SI
definition of the metre, which fixes the speed of light in vacuum exactly:

$$c = 299{,}792{,}458\ \text{m/s} \implies \frac{1}{c} = 3.335641\ \mu\text{s/km}$$

| Medium | Index | Delay | Derivation |
|---|---|---|---|
| Vacuum (hard floor) | $n = 1$ | $3.335641\ \mu\text{s/km}$ | $1/c$ |
| Microwave / free-space RF | $n_{\text{air}} \approx 1.0003$ | $3.336642\ \mu\text{s/km}$ | $1.0003 / c$ |
| Single-mode fiber (physics) | $n_g = 1.4682$ | $4.897388\ \mu\text{s/km}$ | $1.4682 / c$ |
| Single-mode fiber (planning default) | — | $5.000\ \mu\text{s/km}$ | Convention: 200 km/ms |

Rules this skill enforces in code:

1. **No link may be modelled faster than $1/c$.** A `propagation_us_per_km`
   override below $3.335641\ \mu\text{s/km}$ raises `ValueError`.
2. **The "3.33 µs/km / 300 km/ms" microwave shorthand MUST NOT be used.** It
   implies 300,000 km/s, which is faster than light in vacuum. It is the reason
   this skill's own earlier constant (3.333) was replaced.
3. **The fiber planning default is intentionally pessimistic.** $5.0\ \mu\text{s/km}$
   is slower than the $4.897\ \mu\text{s/km}$ physics value, so a budget built on
   it cannot promise a latency the medium is unable to deliver. Use
   `PROPAGATION_PHYSICS_FIBER_US_KM`, or a carrier-measured figure, only when the
   optimistic bound is what you actually want.

$n_g = 1.4682$ is the group index of Corning SMF-28 at 1550 nm, from the Corning
SMF-28e product information sheet; the corresponding ~4.9 µs/km figure is the
standard telecom planning number for that fiber. The microwave constant
cross-checks against the widely quoted free-space figure of ~5.4 µs per statute
mile ($3.336642 \times 1.609344 = 5.3698\ \mu\text{s/mile}$).

## Distance measurement

| Requirement | Standard |
|---|---|
| Distance basis | `distance_km` MUST be **route** distance (physical cable or line-of-sight path). Great-circle distance is not acceptable input on its own. |
| Circuity | Where only straight-line distance is known, `route_circuity_factor` MUST be set. Fiber rights-of-way typically add 15-30%. A factor below 1.0 is rejected. |
| Microwave routes | Line-of-sight chains follow tower siting, not a straight line, and each radio hop adds latency beyond propagation. Model the tower-chain distance and charge per-hop radio latency through `num_switches` / `switch_latency_us`. |

## Equipment latency anchors

| Device class | Published port-to-port latency | Source |
|---|---|---|
| Layer 1 switch (Arista 7130 Connect) | 4 ns | Arista 7130 Connect Series data sheet |
| Ultra-low-latency L2/L3 switch (Arista 7150) | 350 ns | Arista 7150S data sheet |

`switch_latency_us` defaults to 0.350 µs (the L2/L3 anchor). Substitute the
figure from the data sheet of the hardware actually racked — the two classes
above differ by ~90x per hop.

## Round-trip and cross-connect

| Requirement | Standard |
|---|---|
| Symmetry | `total_rtt_us` doubles the one-way figure and is valid **only** for a symmetric path. Asymmetric media (microwave out / fiber back) MUST use `calculate_round_trip_budget(outbound, inbound)`. |
| Patch cord equivalence | All cross-connect cables within the same cage MUST be ordered with matched lengths to avoid intra-rack skew between strategy servers. |
| Cross-connect delay | In-building patch delay MUST be entered as `cross_connect_us` and appears as a distinct term in the budget, never folded into propagation. |

## Scoring

| Requirement | Standard |
|---|---|
| Normalisation | Min-max over the rankable candidate set, 0 = best. A degenerate spread (all candidates equal) yields 0.0 for all, not a division by zero. |
| Score direction | `composite_score` is a **penalty**: lower is better, range [0, 1]. |
| Weights | Rescaled to sum to 1.0. Negative, non-finite, or all-zero weights are rejected. |
| Missing data | A facility with no modelled link MUST be reported unranked with `avg_rtt_us=None`. Substituting 0.0 would rank the least-understood candidate first. |
| Comparability | Scores are relative to the candidate set supplied in a single call and MUST NOT be compared across runs with different candidate sets. |

## Sources

- SI definition of the metre / exact value of $c$ — BIPM SI Brochure.
- Corning SMF-28e Optical Fiber Product Information (group index 1.4682 at 1550 nm):
  https://princetel.com/wp-content/uploads/2023/12/SMF28e.pdf
- Arista 7150S Series data sheet (350 ns port-to-port):
  https://www.arista.com/assets/data/pdf/Datasheets/7150S_Datasheet.pdf
- Arista 7130 Connect Series (4 ns Layer 1 port-to-port):
  https://www.arista.com/en/products/7130-connect
- Laughlin, Aguirre & Grundfest, *Information Transmission Between Financial
  Markets in Chicago and New York* (arXiv:1302.5966) — documents a ~3 ms
  reduction in one-way Chicago-New York communication time between April 2010
  and August 2012 as fiber and microwave routes were built out. Absolute
  per-route latencies frequently attributed to this corridor (~6.65 ms fiber,
  ~4.1 ms microwave) circulate through secondary sources and are **not**
  independently verified here.
