# Standards for New Zealand Exchange (NZX) API

| Metric | Engineering Standard |
|---|---|
| TargetCompID | Must be set to `NZX_TRADING`. |
| Settlement Currency | FIX Tag `15=NZD` MUST be included. |
| Tick Size (< $0.20) | $0.001$ NZD. |
| Tick Size ($0.20 - $1.995) | $0.005$ NZD. |
| Tick Size ($\ge $2.00) | $0.01$ NZD. |
