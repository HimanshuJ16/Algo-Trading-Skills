# Deep Workflow Reference — etrade-oauth1-signature-flow

## Full Procedure

### Leg 1 — Request token (GET)

1. Build the header with `build_request_token_header()`. It sets `oauth_callback="oob"`,
   which E\*TRADE requires and does not default.
2. Sign with the consumer secret and an **empty** token secret; the signing key still ends
   in `&`.
3. `GET {base}/oauth/request_token` with that `Authorization` header.
4. Parse the form-encoded body with `parse_token_response()`, then `set_request_token()`.
   An error body parses as valid form data, so a parser that shrugs at a missing
   `oauth_token` hands you a client that signs everything with empty credentials.
5. Start a 5-minute clock. Past that, the request token is dead — go back to step 1 rather
   than retrying leg 3.

### Leg 2 — User authorization (browser, human)

6. `get_authorize_url()` → `https://us.etrade.com/e/t/etws/authorize?key=…&token=…`.
   The host is `us.etrade.com` in **both** sandbox and production. Both query values are
   percent-encoded; request tokens contain `+`, `/`, and `=`.
7. The user approves and reads back the `oauth_verifier` from the Authorization Complete
   page, or receives it appended to a registered callback URL.

### Leg 3 — Access token (GET)

8. `build_access_token_header(verifier)` signs with the consumer secret **and the request
   token secret**, carrying `oauth_token` (the request token) and `oauth_verifier`.
9. `GET {base}/oauth/access_token`, parse with `parse_token_response()`, store with
   `set_access_token()`.

### Steady state — per-request signing

10. `sign_request(method, url, extra_params=None)` returns `{"Authorization": ...}`.
    Pass the URL **exactly as it will be sent**, query string included. Put form-encoded
    body fields in `extra_params`; they are signed but not emitted in the header
    (RFC 5849 §3.5.1).
11. Nonce and timestamp are generated per call. Never cache a signed header.

### Token lifecycle

12. **Idle inactivation (2 hours, no requests):** `sign_renew_access_token()` against
    `{base}/oauth/renew_access_token`. A strategy that trades the open and then goes quiet
    is inactivated mid-session; either keep a low-rate keepalive inside the 2-hour window
    or renew on the first 401 after a quiet period.
13. **Daily expiry (end of calendar day, US Eastern):** renewal does not help. The full
    three-legged flow must run again, with a human. Schedule this before the session, and
    note that it is a genuine unattended-operation constraint, not a scheduling detail.
14. **Shutdown or suspected compromise:** `sign_revoke_access_token()`.

## Failure triage

| Symptom | Likely cause | Action |
|---|---|---|
| 401 on every call, including the first | Base string wrong — query params omitted, URL not normalized, or params sorted before encoding | Reproduce the RFC 5849 §3.4.1.1 vector in a unit test |
| Request-token call rejected | `oauth_callback` missing | Send `oauth_callback="oob"` |
| Authorize page 404 | URL built from the API host | Use `https://us.etrade.com/e/t/etws/authorize` |
| Authorization approves an unknown token | Request token not percent-encoded in the query | Encode `+`, `/`, `=` |
| Leg 3 rejected after a slow user | Request token older than 5 minutes | Restart at leg 1 |
| Intermittent rejections that track host uptime | Clock drift beyond ±5 minutes | Sync NTP; see `clock-drift-monitoring-alerting-thresholds` |
| 401 after a quiet period, recovering after re-auth | 2-hour idle inactivation | `renew_access_token` |
| 401 after midnight ET that renewal never fixes | Daily expiry | Full three-legged flow |
| Signature rejected only on symbol lists or text fields | Params sorted before percent-encoding | Encode first, then sort |

## Production Implementation Reference

- Code: `scripts/etrade_auth.py` (`ETradeOAuth1Client`, `ETradeAuthError`).
- Tests: `scripts/test_etrade_auth.py` — includes the RFC 5849 §3.4.1.1 base string vector
  and the X OAuth 1.0a published signature vector.
- The module performs signing only. It issues no HTTP requests and adds no dependencies;
  transport, timeouts, and retry policy are the caller's.
