# Workflows for Dark Pool Routing Logic

1. **Venue Toxicity & Fill Rate Audit**:
   - Filter candidate dark pools by toxicity ceiling ($\le \text{MaxToxicity}$).
2. **Allocation Weighting**:
   - $S_v = \text{FillRate}_v \times (1.0 - \text{Toxicity}_v / 50.0)$.
3. **Child Slicing & MinQty Attachment**:
   - Divide parent block across qualified venues.
   - Enforce anti-pinging $\text{MinQty}$ parameters.
4. **Execution Routing**:
   - Dispatch non-displayed IOC/Dark orders via FIX protocol.