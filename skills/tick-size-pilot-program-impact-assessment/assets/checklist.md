# Institutional Tick Size Pilot Program Impact Checklist

## Pre-Analysis Data Hygiene & Setup
- [ ] **L1/L2 Tick Data Collection**: Ingest sub-second quote and trade messages for baseline and test periods.
- [ ] **Trade Aggressor Tagging**: Verify trades are correctly tagged with aggressor side (Buy vs Sell) via Lee-Ready or exchange execution records.
- [ ] **Post-Trade Midpoint Alignment**: Match trades with 5-minute future midpoint prices for realized spread decomposition.

## Spread & Liquidity Metric Calculation
- [ ] **Quoted Spread Computation**: Calculate average quoted spread across trading hours (excluding open/close auction periods).
- [ ] **Effective Spread Computation**: Decompose effective spread to evaluate market taker execution costs.
- [ ] **Realized Spread & Adverse Selection**: Compute 5-minute realized spread and adverse selection in basis points.
- [ ] **L1 Book Depth**: Calculate average shares posted at the national best bid/offer (NBBO).
- [ ] **Order-to-Trade Ratio (OTR)**: Track quote update volume vs execution counts to measure order flickering.

## Algorithm Recalibration & Deployment
- [ ] **Passive Order Queue Priority**: Update pegged order offsets and child order sizes for expanded $0.05 queues.
- [ ] **Aggression Threshold Tuning**: Adjust market order crossing thresholds in TWAP/VWAP execution engines.
- [ ] **Backtest Recalibration**: Update backtest simulator transaction cost models with post-pilot spread and depth parameters.