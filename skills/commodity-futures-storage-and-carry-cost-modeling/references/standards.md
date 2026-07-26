# Standards for Commodity Carry Cost Modeling

| Metric | Engineering Standard |
|---|---|
| Continuous Compounding | All cost of carry and convenience yield formulas MUST use continuous compounding ($e^{x}$). |
| Maturity Precision | Maturity $T$ MUST be calculated using exact day fractions ($T = \text{days\_to\_expiry} / 365.0$). |
| Convenience Yield Bounds | A convenience yield $y < 0$ MUST trigger an inspection alert, as continuous negative convenience yield is rare and indicates bad spot/futures price sync. |