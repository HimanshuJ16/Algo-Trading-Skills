# Standards for MT5 Python Bridge

| Metric | Engineering Standard |
|---|---|
| Volume Data Type | Volume MUST be passed as a `float` (e.g. `0.1`), NOT `int`. |
| Retcode Verification | Order success MUST verify `retcode == 10009` (`TRADE_RETCODE_DONE`). |
| Filling Type Standard | Orders MUST explicitly specify `type_filling` (`ORDER_FILLING_IOC` or `FOK`). |
