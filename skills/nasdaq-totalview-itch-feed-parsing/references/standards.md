# Standards for Nasdaq TotalView-ITCH Feed Parsing

| Metric | Engineering Standard |
|---|---|
| Byte Order | Big-Endian `>` MUST be used for all struct unpacking. |
| Price Divisor | Raw integer price MUST be divided by $10,000.0$. |
| Timestamp Format | 6-byte 48-bit nanoseconds from midnight. |
