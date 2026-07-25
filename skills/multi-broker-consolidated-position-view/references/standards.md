# Broker Integration Standards — multi-broker-consolidated-position-view

| Metric | Calculation | Description |
|---|---|---|
| Net Quantity | $Q_{\text{net}} = \sum_b Q_b$ | Algebric sum of long and short holdings |
| Gross Quantity | $Q_{\text{gross}} = \sum_b \|Q_b\|$ | Total capital exposure magnitude |
| Base Currency | USD | Default accounting currency |
| Discrepancy Threshold | $\|Q_{\text{actual}} - Q_{\text{expected}}\| > 10^{-5}$ | Reconciliation drift trigger |

## Category

`broker-integration` — see top-level `mappings/` directory.
