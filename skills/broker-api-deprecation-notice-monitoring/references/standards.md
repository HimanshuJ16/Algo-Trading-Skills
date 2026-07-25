# Broker Integration Standards — broker-api-deprecation-notice-monitoring

| Header / Standard | Example Value | Description |
|---|---|---|
| RFC 8594 `Sunset` | `Wed, 11 Nov 2026 00:00:00 GMT` | Scheduled API retirement timestamp |
| `Deprecation` | `true` | Indicates endpoint is deprecated |
| `X-API-Deprecation-Warning` | `"V1 retired in 30 days"` | Custom broker deprecation message |
| Critical Horizon | $D \le 7$ days | Immediate engineering escalation threshold |

## Category

`broker-integration` — see top-level `mappings/` directory.
