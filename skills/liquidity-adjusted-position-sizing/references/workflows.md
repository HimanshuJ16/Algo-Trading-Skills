# Deep Workflow Reference — liquidity-adjusted-position-sizing

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Compute Liquidity Capacity**:
   $$\text{DailyCapacity} = \alpha_{\text{max}} \times \text{ADV}_{20d}$$
2. **Compute Max Allowed Position Shares**:
   $$\text{MaxShares} = \text{DailyCapacity} \times DTL_{\text{max}}$$
3. **Apply Position Cap**:
   $$\text{FinalShares} = \min\left(\frac{V_{\text{target}}}{S}, \text{MaxShares}\right)$$
4. **Log Scaling Factor**: Record reduction factor if target allocation was scaled down.

## Production Implementation Reference

- Reference code: `scripts/liquidity_position_sizer.py` (`LiquidityPositionSizer`, `LiquiditySizingResult`).
- Automated unit tests: `scripts/test_liquidity_position_sizer.py`.
