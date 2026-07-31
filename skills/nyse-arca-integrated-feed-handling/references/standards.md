# Standards for NYSE Arca Integrated Feed Handling

| Metric | Engineering Standard |
|---|---|
| Byte Ordering | Little-Endian `<` MUST be used for all XDP struct unpacking. |
| Price Scaling Divisor | Raw integer price MUST be divided by $10,000.0$. |
| Symbol Mapping | `SymbolIndex` MUST be resolved via Symbol Index Mapping table. |
