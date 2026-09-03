---
name: sandbox-vs-production-endpoint-drift
description: >-
  Use before promoting a broker integration from sandbox to live, to compare captured
  responses for nested schema drift, nullability changes, rate-limit header differences,
  status-code class changes and endpoints missing from one environment.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: schema-drift, sandbox-parity, api-contract, broker-integration, devsecops, promotion-gate
  brokers_frameworks: "Alpaca Paper Trading API; Binance Spot Test Network; RFC 9110 HTTP Semantics; IETF RateLimit Header Fields (Internet-Draft)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this before promoting a broker integration from a sandbox, testnet or paper
environment into live trading, and again whenever either environment's API version
changes. Sandbox environments drift from production silently: brokers deploy upcoming
features to a testnet first, alter production payloads without updating paper-trading
documentation, and expose only part of the production endpoint surface. Schema drift —
a nested field that changed from a JSON number to a string, a field production stopped
returning, a null where the sandbox always had a value, a rate-limit header the sandbox
never emits — surfaces as a parser crash or an unhandled throttle on the first live order.

The tool is a **promotion gate**, and its failure modes are asymmetric: a false positive
costs a developer a few minutes, a false negative promotes an integration that was
rehearsed against a contract production does not honour. Its severity model is biased
accordingly.

## When NOT to Use

- **As proof that the sandbox is a faithful rehearsal.** It compares structure, not
  behaviour. Alpaca states plainly that "the API spec is the same between the paper
  trading and live accounts" while paper order quantity "is not checked against the NBBO
  quantities" and eligible orders "receive partial fills for a random size 10% of the
  time". Every one of those differences passes this audit cleanly. Execution realism is
  the subject of `demo-account-realism-gap-assessment`.
- **As a live prober.** It takes response samples you have already captured. It issues no
  requests, holds no credentials, and cannot tell you a field is *always* present — only
  that it was present in the sample you supplied. Capture the same request in both
  environments, under comparable conditions, before comparing.
- **As a specification differ.** It compares captured responses, not OpenAPI documents. To
  diff two published schema versions — request bodies, required-parameter transitions,
  enum changes — use `broker-api-changelog-diffing-tool`.
- **On payloads whose arrays are heterogeneous.** Arrays are compared using their first
  element as representative, so a list mixing element shapes is only partially compared.
- **As a value comparator.** Two `str` fields with different contents are not drift here;
  only shapes, presence, nullability and headers are compared.

## Prerequisites

- Decoded JSON responses (Python mappings) for the *same* request against both
  environments: `sandbox_json`, `prod_json`. Not raw text — parsing is the caller's job.
- Response headers and status codes for that request: `sandbox_headers`, `prod_headers`,
  `sandbox_status`, `prod_status`.
- Optionally, the list of endpoint paths each environment exposes, for
  `compare_endpoint_inventory`.
- Samples captured under comparable conditions. A sandbox response captured out of hours
  against a production response captured mid-session measures the session, not the
  environment.

## Workflow

1. **Capture like for like, then gate on the combined report.** Call `audit_endpoint`,
   not `compare_schemas` alone: the schema report's `passed` flag knows nothing about
   header or status-code drift, so gating on it is the easiest way to get a green light
   for an endpoint that has plainly drifted. `report.exit_code` is 0 when no CRITICAL
   finding was raised and 1 otherwise; `report.format_report()` renders findings
   severity-first.

2. **Let the detector reject unusable input.** `DriftAuditError` is raised when a payload
   is not a mapping, when both payloads are empty, or when a status code is not an
   integer in 100–599. This is deliberate: a failed capture yields an empty dict, and a
   comparator that shrugs and reports parity turns the gate green at exactly the moment
   it matters. For an endpoint that genuinely returns no body, pass no payloads and audit
   headers and status codes instead.

3. **Compare payloads recursively, not key-by-key at the top level.** Broker responses
   nest — an order inside an envelope, legs and fills inside the order. Findings carry the
   dotted path (`order.legs[0].price`) where the drift was found.

4. **Treat absence and null as drift in both directions.** A field present only in
   production is a surface the sandbox never exercised; a field present only in the
   sandbox raises `KeyError` the first time the code runs live. Both are CRITICAL. A null
   on one side and a value on the other is CRITICAL too — the code will meet a type it was
   never tested against. An array populated in production but empty in sandbox is CRITICAL
   (its element schema was never rehearsed); an array empty in the production *sample* is a
   WARNING, because those elements were not compared at all.

5. **Distinguish numeric widening from a type change.** `int` versus `float` is a WARNING;
   `float` versus `str` is CRITICAL. `bool` is a subclass of `int` in Python, but JSON
   `true` is not JSON `1`, so a bool/number pair is CRITICAL rather than a widening.

6. **Audit headers for presence, quota value, and content type.** Field names are matched
   case-insensitively (RFC 9110 §5.1). A rate-limit field present in production but absent
   from the sandbox is a WARNING — throttling handling is unrehearsed. A sandbox
   advertising a *more permissive* quota than production is CRITICAL: pacing calibrated
   against it will breach the production limit and have live orders rejected. A
   content-type media type that differs (`application/json` versus `text/html`) is
   CRITICAL, since the decoder will not match live; a differing `charset` parameter is not
   drift.

7. **Grade status codes by class, not by numeric distance.** A sandbox 200 against a
   production 429 or 500 is a different outcome class (RFC 9110 §15) and blocks promotion;
   400 versus 404 is a same-class difference worth reviewing. Numeric distance conflates
   the two — 404 and 500 differ by 96, 200 and 299 by 99.

8. **Compare the endpoint inventory when a whole family may be missing.** Binance's Spot
   Test Network exposes only `/api` endpoints, not `/sapi`, so an integration can pass
   every payload-level check and still call a path that does not exist in the environment
   it was rehearsed against.

> Full procedure: see `references/workflows.md`.
> Severity contract and classification rationale: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Comparing only the top-level keys.** Broker payloads nest. A comparator that diffs the
  envelope reports "no drift" for a response in which every nested price changed from a
  JSON number to a string — the exact failure this skill exists to catch, reported as a
  pass.
- **Skipping fields that are null on either side.** A sandbox that always returns
  `"filled_at": null` never exercised the timestamp parse path that production will hand
  the code on the first fill.
- **Gating on a partial report.** Running the schema, header and status audits and then
  gating on the schema report alone discards the other two.
- **Reading an empty comparison as parity.** Two empty payloads mean the capture failed,
  not that the environments agree.
- **Grading status-code drift by numeric distance.** 404 against 500 is a server error
  where the sandbox reported a missing resource; the codes differ by less than 100.
- **Assuming identical schemas imply identical behaviour.** Alpaca's paper environment
  shares the live API spec but fills orders from a simulation model that ignores available
  liquidity — a clean drift report says nothing about that.
- **Trusting sandbox rate limits.** A testnet quota is often more generous than
  production's. Pacing tuned there produces 429s on live order submission, where a retry
  loop on an ambiguous order state is precisely what must not happen.
- **Comparing one sample and calling the contract proven.** Optional fields appear only in
  some responses; a single pair of samples cannot establish that a field is always present
  or never null.
- **Comparing samples captured under different conditions.** A sandbox response taken out
  of session against a production response taken at the open measures the session.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/sandbox-vs-production-endpoint-drift/scripts`
- Build a payload whose drift is nested two levels down inside an array element and
  confirm the finding's `field_name` is the dotted path. A comparator that passes every
  flat-payload test can still fail this one, which is the case that matters.
- Confirm two empty payloads raise `DriftAuditError` rather than reporting parity, and
  that a non-mapping payload raises rather than raising `AttributeError` deep in the walk.
- Confirm `audit_endpoint` with clean payloads but a 200/400 status pair reports
  `passed=False` and `exit_code == 1`.
- Confirm 404 against 500 is CRITICAL while 400 against 404 is WARNING.
- Confirm a sandbox `x-ratelimit-limit` higher than production's is CRITICAL, and that
  `Content-Type` differing only by `charset` is not a finding.
- Confirm findings are emitted in a deterministic order across runs.

## Related Skills

- `sandbox-credential-leakage-prevention`
- `broker-api-changelog-diffing-tool`
- `demo-account-realism-gap-assessment`
- `paper-to-live-promotion-checklist`
- `environment-parity-dev-staging-production`
