# Broker & Framework Coverage — broker-account-margin-call-handling

| Broker / Margin System | Margin Warning Trigger | Forced Liquidation Threshold | Pre-Trade Margin Impact |
|---|---|---|---|
| Interactive Brokers (Reg T / PM) | Excess Liquidity $< 10\%$ of NLV | Maintenance Margin $>$ Net Liquidation Value | Check Impact API `reqsecdefoptparams` |
| Zerodha RMS | Margin Utilization $\ge 85\%$ | Square-off triggered at 100% margin breach | Margin Calculator API |
| Alpaca Margin API | Equity $<$ Maintenance Margin | Day trade / maintenance margin call | Predictive Initial Margin check |
| CME SPAN | Configurable warning buffer | Intraday Initial Margin breach | SPAN array calculations |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

- **Reg T vs Portfolio Margin**: Portfolio margin calculates risk based on maximum theoretical loss within a predefined range (e.g., +/- 15%). Reg T uses fixed percentages. The engine abstracts this via `maintenance_margin`.
- **FINRA Rule 4210**: Requires firms to collect margin. Failure to act proactively results in broker violations and possible account freezes.
- **Liquidity Spiral Risk**: Liquidating large illiquid positions at market can trigger a collapse in NLV, creating a secondary margin call. Therefore, `plan_deleveraging` must use an ADV cap (Average Daily Volume participation rate limit).
- **Tail Risk**: Short unhedged options carry the highest tail risk and are the first priority for liquidation in a margin stress scenario, regardless of PnL impact.
