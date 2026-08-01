# Workflows for Reinforcement Learning Safety Constraints for Execution

1. **Spread Veto Guard**:
   - Veto action (set qty = 0) if current spread > max_spread.
2. **Terminal Inventory Clearance**:
   - Force liquidation action if time remaining <= terminal_horizon_sec.
3. **Action Clipping & Position Cap**:
   - Clip order size to max_order_size and position cap to max_inventory.
4. **Reward Penalty Shaping**:
   - Deduct penalty_lambda from reward upon interception to train safe policies.
