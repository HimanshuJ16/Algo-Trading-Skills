# Pre-Flight Checklist — ESG Cross-Vendor Signal

## Normalization

- [ ] Are raw scores passed in **vendor-native units**, with no pre-scaling upstream?
- [ ] Is each vendor normalized against its **published band structure** rather than a nominal min/max — MSCI band mid-points, Sustainalytics bounded at the Severe floor (40), LSEG/Refinitiv divided by 100?
- [ ] Has anyone re-introduced end-point letter mapping ($\text{AAA}=1.0$, $\text{CCC}=0.0$) or a divide-by-100 Sustainalytics rescale?
- [ ] Are all three normalized outputs on $[0.0, 1.0]$ with **higher = better**, including the inverted Sustainalytics risk score?

## Data integrity

- [ ] Does `None` mean "vendor does not cover this issuer" and nothing else?
- [ ] Do NaN, infinity, out-of-range values and unrecognised MSCI tokens **raise** rather than being clipped, coerced, or dropped from the consensus?
- [ ] Are consensus and dispersion `None` — never $0.0$ — when undefined?
- [ ] Would a downstream cross-sectional ranker consuming this report ever read an uncovered or excluded issuer as the worst ESG name in the universe?

## Signal logic

- [ ] Is the vendor coverage floor (`min_vendors_for_conviction`, default 2) enforced before any directional signal?
- [ ] Is the disagreement gate applied **symmetrically** — suppressing laggard calls as well as leader calls?
- [ ] Are `leader_threshold`, `laggard_threshold` and `disagreement_threshold` calibrated on this universe out-of-sample, rather than left at the illustrative defaults?
- [ ] With two vendors, is it understood that $\sigma_{\text{esg}} > 0.25$ requires the normalized scores to differ by more than $0.5$?

## Exclusions

- [ ] Are rule-based exclusion screens resolved **upstream** (revenue thresholds, treaty lists, UNGC/OECD status) rather than inferred from a consensus score?
- [ ] Do downstream consumers gate on `signal` / `exclusion_reasons` rather than on `consensus_esg_score`, which is retained on excluded records for audit only?
- [ ] Is the applicable mandate identified by jurisdiction — EU PAB (Del. Reg. 2020/1818 Art. 12), SFDR PAI 14 (Del. Reg. 2022/1288), or a client-specific list?

## Point-in-time and backtest hygiene

- [ ] Does every record carry a timezone-aware `as_of` vintage stamp?
- [ ] Are vendor scores queried as-of the trading date from a point-in-time store, not rebuilt from a current extract?
- [ ] Is the universe built from point-in-time constituents rather than today's vendor coverage?
- [ ] Has the backtest window been checked for vendor **methodology-version breaks**, with each side treated as a separate variable?

## Reference-frame and compliance sanity

- [ ] Is it documented that MSCI is industry-relative, Sustainalytics absolute, and LSEG governance benchmarked by country of incorporation — and that the consensus therefore blends three different questions?
- [ ] If any client-facing artefact reproduces vendor ESG ratings inside the EU, has each vendor been checked against the ESMA Article 14 register (Regulation (EU) 2024/3005, applicable from 2 July 2026)?
