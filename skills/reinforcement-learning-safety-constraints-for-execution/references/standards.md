# Financial ML Standards — reinforcement-learning-safety-constraints-for-execution

| Risk Constraint | Enforced Mechanism | Action Transformation |
|---|---|---|
| Max Order Size | Clipping | $\text{Sign}(\Delta Q) \cdot \min(|\Delta Q|, \text{MaxOrderSize})$ |
| Position Limit Cap | Hard Cap | Clip to remaining capacity $Q_{\text{max}} - |Q|$ |
| Wide Spread Veto | Action Masking | Set $\Delta Q = 0.0$ if $\text{Spread} > \text{MaxSpread}$ |
| Terminal Inventory Clearance | Policy Override | Force liquidation $\Delta Q = -Q_{\text{current}}$ near session close |

## Category

`financial-ml` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with SEC Rule 15c3-5 risk controls, autonomous trading agent safety governance, and institutional algorithmic execution standards.
