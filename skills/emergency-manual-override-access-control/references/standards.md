# Standards for Emergency Manual Override Access Control

| Metric | Engineering Standard |
|---|---|
| Dual Sign-Off Rule | Critical kill switches (`KILL_SWITCH_ALL_ALGOS`) MUST require dual operator authorization. |
| Audit Trail Rule | ALL manual overrides MUST generate an immutable SHA-256 hash log with justification notes. |
| Auto-Expiry Window | Overrides MUST specify a time-to-live (TTL $\le 60\text{ mins}$) to prevent indefinite halts. |