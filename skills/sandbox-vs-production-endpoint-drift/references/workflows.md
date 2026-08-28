# Workflows for Sandbox vs Production Endpoint Drift

The full procedure behind `SKILL.md`. Severity definitions and their rationale live in
`standards.md`.

## 1. Capture comparable samples

Drift can only be measured between samples that differ in nothing but the environment.

- Issue the **same request** — same path, method, query parameters and body — against the
  sandbox and production base URLs. Brokers use different hosts per environment (Alpaca's
  paper base URL is `https://paper-api.alpaca.markets`; Binance's Spot Test Network is
  `https://testnet.binance.vision/api`), each with its own credentials.
- Capture under **comparable conditions**: same instrument, same session state, same
  account posture. A sandbox response captured out of hours against a production response
  captured at the open measures the session, not the environment.
- Capture the **response body, the headers, and the status code** together. Headers alone
  cannot tell you the payload drifted, and the payload alone cannot tell you the endpoint
  now throttles.
- Prefer a **populated** sample. An empty `fills` or `legs` array carries no element
  schema, and the audit will say so rather than pretend the elements matched.
- Redact credentials before storing samples. Captured responses can echo account
  identifiers; see `sandbox-credential-leakage-prevention`.
- Recapture after any environment reset. Binance resets the Spot Test Network to a blank
  state roughly monthly without notice, which invalidates earlier captures.

## 2. Run the combined audit

```python
from drift_detector import EndpointDriftDetector

detector = EndpointDriftDetector()
report = detector.audit_endpoint(
    "/v2/orders",
    sandbox_json=sandbox_body,
    prod_json=prod_body,
    sandbox_headers=sandbox_headers,
    prod_headers=prod_headers,
    sandbox_status=sandbox_status,
    prod_status=prod_status,
)
print(report.format_report())
raise SystemExit(report.exit_code)
```

`audit_endpoint` runs every stage and folds the findings into one report. The individual
methods (`compare_schemas`, `compare_headers`, `compare_status_codes`,
`compare_endpoint_inventory`) remain available for targeted checks, but gating a promotion
on `compare_schemas(...).passed` alone ignores header and status drift entirely.

Stages with no samples are skipped, so an endpoint that returns no body is audited on its
headers and status code alone. A body captured in only one environment is itself reported
as drift.

## 3. Payload schema comparison

The comparison is recursive and shape-based:

- **Objects** are compared key by key, in sorted order, and recursed into. Findings carry
  the dotted path where the drift was found (`order.legs[0].price`).
- **Arrays** are compared using their first element as representative — enough to catch a
  changed element schema without emitting one finding per row of a long fill list, and a
  documented limitation for heterogeneous arrays. An array empty in sandbox but populated
  in production is CRITICAL; an array empty in the production sample yields a finding
  stating the elements were *not compared*.
- **Nulls** are handled before types: null on one side and a value on the other is
  CRITICAL in both directions. Null on both sides is not drift, but it is also not
  evidence — neither environment exercised the field.
- **Scalars** are compared by Python type. `int`/`float` is a WARNING; everything else is
  CRITICAL. `bool` is excluded from the numeric pair because JSON `true` is not JSON `1`.
- **Strings are not arrays.** `str` and `bytes` are sequences in Python; comparing them
  element-wise would be meaningless, so only `list` and `tuple` are treated as JSON
  arrays.
- **Values are never compared**, only shapes. Two `str` fields with different contents are
  not drift.

Recursion stops at `max_depth` (default 20) and records a WARNING naming the subtree that
was not compared, rather than raising `RecursionError` inside a CI job.

## 4. Header and rate-limit auditing

Field names are lower-cased before comparison (RFC 9110 §5.1). Three checks run:

1. **Presence** of the rate-limit family — `x-ratelimit-*` and `x-rate-limit-*` (de-facto
   vendor conventions), `ratelimit` and `ratelimit-policy` (draft-ietf-httpapi-ratelimit-
   headers, still an Internet-Draft), and `retry-after` (RFC 9110 §10.2.3). Present in
   production and absent in sandbox is a WARNING: the back-off path is unrehearsed.
2. **Quota value** on `x-ratelimit-limit` / `x-rate-limit-limit`. A sandbox quota *higher*
   than production's is CRITICAL — pacing tuned against it will breach the live limit.
   Values are parsed as a leading integer, tolerating policy-annotated forms such as
   `100, 100;w=60`; anything non-numeric is skipped rather than guessed at.
3. **Content type**, compared on the media type alone. `application/json` against
   `text/html` is CRITICAL; a differing `charset` parameter is not drift.

## 5. Status-code verification

Send the same request — including the same *invalid* request, which is where sandboxes
most often diverge — and compare the codes. Severity follows the status class (RFC 9110
§15), not numeric distance: a class change (200 against 429, 404 against 500) is CRITICAL;
a same-class difference (400 against 404) is a WARNING. Codes outside 100–599, and
non-integers, raise `DriftAuditError` rather than being scored.

## 6. Endpoint inventory comparison

```python
findings = detector.compare_endpoint_inventory(sandbox_paths, prod_paths)
```

Whole endpoint families can be absent from a sandbox — Binance's Spot Test Network exposes
only `/api`, not `/sapi` — so an integration can pass every payload-level check and still
call a path that does not exist in the environment it was rehearsed against, or that no
longer exists in production. Both directions are CRITICAL.

## 7. Act on the report

- **CRITICAL** findings block the promotion. Fix the adapter, recapture, re-run.
- **WARNING** findings need a recorded decision, not silence — particularly
  `ARRAY_NOT_COMPARED` and `DEPTH_LIMIT_REACHED`, which mean a region was never compared.
- Re-run the audit as a scheduled job, not only at promotion time. Production payloads
  drift after promotion too, and the same report gates a regression.
- A clean report means "no structural drift in these samples". It does not mean the
  sandbox behaves like production: simulated fills, unenforced liquidity checks and
  different matching semantics are invisible here. That gap is measured by
  `demo-account-realism-gap-assessment`, and the promotion decision itself by
  `paper-to-live-promotion-checklist`.
