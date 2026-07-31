# Standards for Moscow Exchange (MOEX) Integration

| Metric | Engineering Standard |
|---|---|
| Exchange Identifier | `SecurityExchange` MUST be set to `MISX` in FIX order tags. |
| Board ID Tagging | Mandatory `BoardID` (`TQBR` Equities, `CETS` FX, `RFUD` Derivatives). |
| Price Collar Ceiling | Orders breaching $\pm 5.0\%$ of reference price MUST be rejected. |
