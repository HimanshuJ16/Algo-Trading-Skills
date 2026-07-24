# Broker & Framework Coverage — broker-account-margin-call-handling

| Broker / Margin System | Margin Warning Trigger | Forced Liquidation Threshold |
|---|---|---|
| Interactive Brokers (Reg T / PM) | Excess Liquidity $< 0$ | Maintenance Margin $>$ Net Liquidation Value |
| Zerodha RMS | Margin Utilization $\ge 85\%$ | Square-off triggered at 100% margin breach |
| Alpaca Margin API | Equity $<$ Maintenance Margin | Day trade / maintenance margin call |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with FINRA Rule 4210 (Margin Requirements), Reg T margin rules, and institutional portfolio risk controls.
