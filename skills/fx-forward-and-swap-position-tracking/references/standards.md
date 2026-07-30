# Standards for FX Forward & Swap Position Tracking

| Metric | Engineering Standard |
|---|---|
| Forward Pricing Engine | Forward rates MUST be priced via Covered Interest Rate Parity (CIRP). |
| Day Count Convention | Money market conventions MUST use Actual/360 for USD/EUR/JPY and Actual/365 for GBP. |
| Valuation Frequency | Mark-to-Market (MtM) PnL MUST be revalued daily. |