# Workflows for Co-Location & Topology Evaluation

1. **Facility Survey**:
   - Collect candidate data center specs (Location, Power Capacity, Cross-Connect Types, Exchange Matching Engine proximity).
2. **Network Route Calculation**:
   - Calculate one-way latency for Fiber ($5.0\ \mu\text{s/km}$) and Microwave ($3.33\ \mu\text{s/km}$).
   - Add switch hop delays (e.g. 200ns per L2 switch).
3. **TCO Modeling**:
   - Sum Rack MRC + Power MRC + Cross-Connect MRC.
4. **Scoring & Ranking**:
   - Rank candidate facilities by composite score: $\text{Score} = w_{lat} \times \text{NormLatency} + w_{cost} \times \text{NormCost}$.