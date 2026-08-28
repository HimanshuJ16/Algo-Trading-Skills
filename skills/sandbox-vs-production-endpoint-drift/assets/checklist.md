# Pre-Flight Checklist — Sandbox vs Production Endpoint Drift

Sign off before promoting a broker integration from sandbox/paper to live trading.

## Capture

- [ ] Same request (path, method, parameters, body) issued against both environments?
- [ ] Captured against the correct per-environment base URL and credentials?
- [ ] Captured under comparable conditions (same instrument, session state, account posture)?
- [ ] Body, headers **and** status code captured for each environment?
- [ ] Samples populated — no empty array standing in for a list of fills, legs or positions?
- [ ] Samples recaptured since the last sandbox reset?
- [ ] Credentials and account identifiers redacted before the samples were stored?

## Payload schema

- [ ] Comparison run recursively, with findings reported at their dotted path?
- [ ] Fields present in production but missing in sandbox reviewed (CRITICAL)?
- [ ] Fields present in sandbox but absent in production reviewed (CRITICAL — this is the
      direction that raises `KeyError` live)?
- [ ] Type mismatches (`str` vs numeric, object vs scalar, `bool` vs number) resolved in the
      payload adapter rather than assumed away?
- [ ] `int`/`float` widenings checked against rounding and tick-size handling?
- [ ] Nullability differences handled in both directions, with both branches tested?
- [ ] Arrays that were empty on one side re-captured, or the gap explicitly accepted?
- [ ] Any `DEPTH_LIMIT_REACHED` finding resolved by raising `max_depth` and re-running?

## Headers and status codes

- [ ] Rate-limit headers present in production also present in the sandbox sample, or
      back-off handling tested against synthetic headers?
- [ ] Request pacing calibrated against the **production** quota, never the sandbox's?
- [ ] Content-type media types identical between environments?
- [ ] Status codes compared for a valid request **and** a deliberately invalid one?
- [ ] Every status-class change (2xx vs 4xx vs 5xx) reconciled before promotion?
- [ ] Error handling keyed on the body's error code, not the status code alone?

## Coverage and gating

- [ ] Endpoint inventories compared — no path exists in only one environment?
- [ ] Promotion gated on `audit_endpoint(...)`, not on `compare_schemas(...).passed` alone?
- [ ] `DriftAuditError` treated as a failed capture, never as a pass?
- [ ] Zero CRITICAL findings, and every WARNING has a recorded decision?
- [ ] Audit wired into CI on `report.exit_code`, and scheduled to re-run after promotion?

## Limits acknowledged

- [ ] Understood that a clean report covers **structure only** — simulated fills,
      unenforced liquidity checks and different matching semantics are not measured here
      (`demo-account-realism-gap-assessment`)?
- [ ] Understood that one sample pair cannot prove a field is always present or never null?
