# Standards for Daylight Saving Time Transition Handling

| Metric | Engineering Standard |
|---|---|
| UTC Invariant Rule | ALL internal market data timestamps and execution logs MUST be stored in UTC nanosecond epochs. |
| IANA Database | Timezone offsets MUST be dynamically computed using the IANA Time Zone Database (never hard-coded fixed offsets). |
| Cross-Border Desync Detection | Trading algorithms spanning US and EU markets MUST audit 2-week March/October DST desynchronization windows. |
