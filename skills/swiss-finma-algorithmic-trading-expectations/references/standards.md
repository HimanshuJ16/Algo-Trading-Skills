# Standards for Swiss FINMA Algorithmic Trading Expectations

| Control # | Requirement | Mandatory Standard |
|---|---|---|
| Ctrl 1 | Pre-Trade Risk Limits | Non-bypassable price collar $\pm 5\%$, max notional cap CHF 500k. |
| Ctrl 2 | Kill Switch | Independent daemon capable of purging orders $< 1.0$s. |
| Ctrl 3 | FinfraG Registry | Formal institutional algorithm inventory registration. |
| Ctrl 4 | Message Throttling | $\le 100$ order messages per second. |
| Ctrl 5 | Audit Trail | Microsecond UTC timestamp precision. |
