# Deep Workflow Reference — sandbox-vs-production-endpoint-drift

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Capture Environment Payload Benchmarks:**
   - Execute identical GET/POST probes against sandbox and production endpoints.
   - Extract raw JSON response bodies, HTTP status codes, and response headers.

2. **Run JSON Schema Comparison:**
   - Execute `EndpointDriftDetector.compare_schemas()`:
     - Check missing fields present in production but absent in sandbox (`CRITICAL`).
     - Check extra fields present in sandbox but absent in production (`WARNING`).
     - Check data type mismatches (e.g., float vs string representations of numbers) (`CRITICAL`).

3. **Audit HTTP Headers & Status Codes:**
   - Audit presence of rate-limit headers (`X-RateLimit-Limit`, `Retry-After`).
   - Compare status code handling for intentionally invalid requests (e.g. 400 vs 200 OK with inline error payload).

4. **Generate Parity Sign-off Report:**
   - Export `EndpointDriftReport`. Block live promotion if `critical_count > 0`.

## Failure Modes Observed in Production

- **Un-Audited Live Deployment:** Promoting paper-tested code directly to live production without auditing response schema parity, triggering runtime type errors.
- **200 OK Error Payload Traps:** Failing to detect that a sandbox returns 200 OK with inline error fields while live production returns HTTP 400 Bad Request.

## Production Implementation Reference

- Reference code: `scripts/drift_detector.py` (`EndpointDriftDetector`, `EndpointDriftReport`, `DriftFinding`).
- Automated unit tests: `scripts/test_drift_detector.py`.
