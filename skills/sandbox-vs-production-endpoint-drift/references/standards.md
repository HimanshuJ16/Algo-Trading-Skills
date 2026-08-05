# Standards for Sandbox vs Production Endpoint Drift

| Drift Category | Severity | Action Required |
|---|---|---|
| Missing Field in Sandbox | CRITICAL | Update sandbox mock / payload parser before live deployment |
| Data Type Mismatch (e.g. float vs str) | CRITICAL | Enforce strict type coercion in payload adapter |
| Rate Limit Header Absence | WARNING | Implement fallback rate-limit handling |
