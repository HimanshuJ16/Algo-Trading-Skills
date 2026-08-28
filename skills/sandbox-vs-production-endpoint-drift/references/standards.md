# Standards for Sandbox vs Production Endpoint Drift

## Severity contract

Severity answers one question: **what happens in production when an integration built and
tested against the sandbox contract is promoted?** It is not a measure of how large the
difference looks.

| Severity | Meaning | Gate behaviour |
|---|---|---|
| `CRITICAL` | Production differs from the sandbox contract in a way that can raise or mis-parse in live trading, or exposes a production surface the sandbox never exercised. | Blocks promotion (`passed=False`, `exit_code=1`). |
| `WARNING` | A difference most parsers tolerate, or a region the audit could not compare. | Does not block; requires review. |
| `INFO` | Observation with no direct integration impact. | Does not block. |

Because this is a gate, the two error costs are not symmetric: a false positive costs a
developer a few minutes, a false negative promotes a broken integration into an order
path. Where a classification is genuinely ambiguous, it is graded upward.

## Drift categories

| Category | Severity | Why | Action required |
|---|---|---|---|
| `MISSING_IN_SANDBOX` (field in production only) | CRITICAL | The integration would be promoted having never seen the field: unknown values, unhandled statuses, untested parsing. | Reproduce the field in the sandbox mock, or handle it explicitly before promotion. |
| `MISSING_IN_PRODUCTION` (field in sandbox only) | CRITICAL | Code written against the sandbox reads the field and raises `KeyError` on the first live response. | Remove the dependency, or read defensively with a documented default. |
| `TYPE_MISMATCH` (e.g. `float` vs `str`, object vs scalar, `bool` vs number) | CRITICAL | Arithmetic or attribute access on the production type fails, or silently coerces wrongly. | Enforce explicit coercion in the payload adapter; do not rely on the environment's type. |
| `NUMERIC_TYPE_MISMATCH` (`int` vs `float`) | WARNING | JSON has one number type and Python arithmetic tolerates the mix, but precision and formatting assumptions can still differ. | Confirm rounding and tick-size handling; normalise to `Decimal` where money is involved. |
| `NULLABILITY_MISMATCH` | CRITICAL | A null where the code expects a value crashes the parser; a value where the sandbox always returned null means the parse path was never exercised. | Make nullability explicit in the adapter and test both branches. |
| `ARRAY_NOT_EXERCISED` (empty in sandbox, populated in production) | CRITICAL | The element schema was never rehearsed. | Capture a sandbox sample with elements present before promoting. |
| `ARRAY_NOT_COMPARED` (empty in the production sample) | WARNING / INFO | The elements were not compared at all — this is a gap in the audit, not evidence of parity. | Recapture with a populated production sample. |
| `BODY_PRESENCE_MISMATCH` | CRITICAL | The environments disagree on whether the endpoint returns a body. | Verify the request is genuinely equivalent, then reconcile. |
| `DEPTH_LIMIT_REACHED` | WARNING | The subtree beyond `max_depth` was **not** compared. | Raise `max_depth` and re-run, or compare that subtree separately. |
| `HEADER_MISSING_IN_SANDBOX` | WARNING | Rate-limit or throttling headers absent in the sandbox mean the client's back-off path is unrehearsed. | Implement and test back-off against synthetic headers. |
| `RATE_LIMIT_VALUE_MISMATCH` (sandbox quota **higher**) | CRITICAL | Pacing calibrated against a more permissive sandbox breaches the production limit; live orders are throttled or rejected. | Pace against the production quota, never the sandbox's. |
| `RATE_LIMIT_VALUE_MISMATCH` (production quota higher) | WARNING | Sandbox pacing is conservative, but production throttling paths remain unrehearsed. | Confirm back-off handling separately. |
| `CONTENT_TYPE_MISMATCH` | CRITICAL | The response decoder will not match live (e.g. a production `text/html` error page against a sandbox `application/json`). | Assert the media type at the transport layer and fail loudly. |
| `STATUS_CLASS_MISMATCH` | CRITICAL | A different outcome class (RFC 9110 §15): success against error, client error against server error. | Reconcile before promotion; never retry blindly across a class change. |
| `STATUS_CODE_MISMATCH` (same class) | WARNING | Both environments failed the request, but classified it differently. | Confirm the error branch keys on the body's error code, not the status alone. |
| `ENDPOINT_MISSING_IN_SANDBOX` / `ENDPOINT_MISSING_IN_PRODUCTION` | CRITICAL | A path that exists in only one environment either cannot be rehearsed, or does not exist live. | Confirm coverage before promotion. |

## HTTP semantics relied on

| Claim | Source | Status |
|---|---|---|
| Header field names are case-insensitive, so `X-RateLimit-Limit` and `x-ratelimit-limit` are the same field. | RFC 9110, *HTTP Semantics*, §5.1 — <https://www.rfc-editor.org/rfc/rfc9110.html#section-5.1> | Internet Standard (STD 97). |
| Status codes are grouped into classes 1xx–5xx, and the class is the primary indicator of outcome. | RFC 9110 §15 (§15.3 2xx, §15.5 4xx, §15.6 5xx) — <https://www.rfc-editor.org/rfc/rfc9110.html#section-15> | Internet Standard. |
| `Retry-After` indicates when a client should retry. | RFC 9110 §10.2.3 | Internet Standard. |
| `429 Too Many Requests` is the throttling status brokers return. | RFC 6585 §4 — <https://www.rfc-editor.org/rfc/rfc6585.html#section-4> | Proposed Standard. |
| `RateLimit` and `RateLimit-Policy` are the fields being standardised for quota advertisement. | draft-ietf-httpapi-ratelimit-headers-11 — <https://datatracker.ietf.org/doc/draft-ietf-httpapi-ratelimit-headers/> | **Internet-Draft, not an RFC** (Standards Track; latest version at the time of writing expires November 2026). Audit for it, but do not assume brokers emit it. |
| `X-RateLimit-*` / `X-Rate-Limit-*` are de-facto vendor conventions. | No standard defines them; they are registered nowhere and their semantics vary by vendor. | Convention only — parse defensively and never assume a shared unit or window. |

## Documented sandbox/production differences (examples, verified)

These are the kinds of divergence this audit exists to surface, and the kinds it cannot
see. Verify the current text against each broker's own documentation before relying on it;
broker environments change without notice.

| Broker | Documented difference | Source | Visible to this audit? |
|---|---|---|---|
| Alpaca | "The API spec is the same between the paper trading and live accounts." | <https://docs.alpaca.markets/us/docs/paper-trading> | The spec being shared is why *payload* drift is subtle here rather than absent. |
| Alpaca | Paper order quantity "is not checked against the NBBO quantities"; eligible orders "receive partial fills for a random size 10% of the time". | Same page | **No** — behavioural, not structural. See `demo-account-realism-gap-assessment`. |
| Alpaca | Paper trading uses a separate base URL (`https://paper-api.alpaca.markets`) and a different API key from the live account. | Same page | Only indirectly: capture against the right base URL, and never promote sandbox credentials (see `sandbox-credential-leakage-prevention`). |
| Binance | "Only the `/api` endpoints are available on the Spot Test Network"; `/sapi` endpoints are not. | <https://testnet.binance.vision/> | **Yes** — via `compare_endpoint_inventory`. |
| Binance | The Spot Test Network "is periodically reset to a blank state", including all pending and executed orders, roughly monthly and without prior notice. | Same page | **No** — state, not schema. Recapture samples after a reset; a comparison against pre-reset captures proves nothing. |
