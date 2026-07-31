# Standards for Minimum Fill Size & Lot Rounding

| Metric | Engineering Standard |
|---|---|
| Board Lot Compliance | Quantities MUST be rounded to exact venue board lot multiples. |
| Minimum Fill Size | Orders below `min_qty` MUST be rejected prior to exchange routing. |
| FIX Protocol Tags | FIX Tag 110 (`MinQty`) and Tag 1089 (`MatchIncrement`) MUST be populated. |
