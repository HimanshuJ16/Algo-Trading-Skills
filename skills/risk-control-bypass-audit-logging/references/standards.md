# Standards for Risk Control Bypass Audit Logging

| Metric | Engineering Standard |
|---|---|
| Audit Immutability | Bypass logs MUST be append-only and tamper-proof (no edits or deletes). |
| Authorization Enforcement | Only principals in the authorized allowlist MAY override critical risk controls. |
| Justification Requirement | Every bypass MUST include a written justification of $\ge 5$ characters. |
| Report Cadence | Risk bypass audit reports MUST be generated daily and retained for regulatory review. |