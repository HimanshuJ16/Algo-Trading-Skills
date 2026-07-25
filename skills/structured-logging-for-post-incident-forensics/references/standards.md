# Standards Reference — structured-logging-for-post-incident-forensics

| Field | Required | Description |
|---|---|---|
| `seq` | Yes | Monotonic sequence number |
| `ts` | Yes | UTC timestamp (epoch float) |
| `event_type` | Yes | Standardized event type enum |
| `correlation_id` | Yes | Links related events across lifecycle |
| `component` | Yes | Source component name |
| `severity` | Yes | INFO, WARNING, ERROR, CRITICAL |
| `message` | Yes | Human-readable description |
| `metadata` | No | Arbitrary key-value pairs |

## Category

`deployment-ops`
