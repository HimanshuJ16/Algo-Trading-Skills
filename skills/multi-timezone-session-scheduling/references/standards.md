# Broker & Framework Coverage — multi-timezone-session-scheduling

| Library / Standard | Relevance to this skill |
|---|---|
| IANA Time Zone Database (tzdb) | Standard IANA timezone strings (`America/New_York`, `Europe/London`, `Asia/Kolkata`). |
| Python `zoneinfo` (Python 3.9+) | Standard library time zone support. |
| ISO 8601 / RFC 3339 | Standard timestamp format representations with explicit UTC offset indicators. |

## Category

`data-management-global` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with exchange official trading hours rules, trade timestamping regulations (MiFID II RTS 25 microsecond clock synchronization mandates), and cross-border settlement schedules.
