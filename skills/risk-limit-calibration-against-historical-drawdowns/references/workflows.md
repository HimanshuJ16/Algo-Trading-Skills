# Workflows for Risk Limit Calibration Against Historical Drawdowns

1. **Returns Ingestion & Metrics Calculation**:
   - Ingest daily returns; calculate max drawdown %, duration, VaR 99%, CVaR 99%, and Ulcer Index.
2. **Stress-Buffered Threshold Calibration**:
   - Apply stress multiplier ($1.5\times$) to historical max DD or EVT tail factor.
3. **Daily Loss Limit & Position Scalar**:
   - Set daily loss limit ($3 \times \text{VaR}_{99}$ USD); compute position scalar if historical DD > 20%.
4. **Report & Audit Generation**:
   - Output calibrated risk limits manifest for risk committee review.