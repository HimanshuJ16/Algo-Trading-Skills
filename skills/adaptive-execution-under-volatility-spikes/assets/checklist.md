# Checklist for Adaptive Execution Under Volatility Spikes

## Deployment Checklist
- [ ] Ensure market data feed has low enough latency to compute micro-volatility accurately.
- [ ] Verify that the `halt_trading` signal correctly cancels existing resting limit orders in the EMS (Execution Management System).
- [ ] Confirm that `limit_offset_bps` adjustments comply with exchange Limit-Up/Limit-Down (LULD) collars.
- [ ] Backtest the volatility thresholds against historical flash crash events (e.g., May 6, 2010, or March 2020) to ensure the circuit breakers trip correctly.