# Pre-Flight / Sign-off Checklist — tastytrade-api-integration

Use this before pointing a Tastytrade integration at a funded account.

## Authentication

- [ ] **No retired flow anywhere.** No `POST /sessions`, `session-token`,
      `remember-token`, or bare `Authorization: {token}` header. Password
      session-token auth was discontinued 2025-12-01.
- [ ] **OAuth2 grant works.** `POST /oauth/token` with `grant_type=refresh_token`
      returns an `access_token`, and requests use `Authorization: Bearer {token}`.
- [ ] **15-minute lifetime respected.** Nothing in the codebase assumes a
      24-hour session. Refresh happens on a buffer, not on a 401.
- [ ] **`expires_in` is never invented.** An implausible value is fatal; an
      absent one falls back to the documented 900s, never longer.
- [ ] **User-Agent is `<product>/<version>`.** Anything else 401s at the edge
      proxy and looks like a token problem it is not.
- [ ] **Refresh token stored as a credential.** Owner-only permissions, outside
      version control, excluded from logs and `repr`.
- [ ] **No response body is interpolated into a log line or exception.** The
      token response travels the same path.
- [ ] **Failed-login backoff exists.** Repeated failures block the source IP for
      roughly 8 hours, including requests that manage open positions.

## Symbology

- [ ] **OCC symbols are exactly 21 characters** and validated, not assumed.
- [ ] **Roots over 6 characters, `CALL`/`PUT`, and non-`YYMMDD` dates raise.**
- [ ] **Strikes are rejected, never rounded.** `200.0001` must not silently
      become the $200 contract; `0.0005` must not round to a zero strike.
- [ ] **Future-option legs do not go through OCC formatting.** They use
      `./ESU4 EW4Q4 240823C5750`, resolved from the future-option chain.
- [ ] **Symbols from chains, configs or upstream signals are parsed and checked**
      against the intended contract before being traded.

## Order construction

- [ ] **Dry run runs before every live submission**, and `is_acceptable` is
      checked — a 2xx dry run can still carry `errors`.
- [ ] **Buying-power effect and projected fees are reviewed**, not just the
      absence of an error.
- [ ] **Price is a magnitude; direction is in `price-effect`.** No negative
      prices are ever sent.
- [ ] **`Market` orders carry no `price` or `price-effect`.**
- [ ] **Leg quantities are positive whole contracts**; duplicate
      `(symbol, action)` legs are rejected.
- [ ] **`external-identifier` is set per submission attempt.**

## Order outcome and reconciliation

- [ ] **Three outcome buckets are handled**: rejected, accepted, ambiguous.
- [ ] **No order id or status is ever fabricated** when the response omits one.
- [ ] **`warnings` on an accepted 2xx order are surfaced**, not discarded.
- [ ] **No ambiguous submission is ever retried.** There is no idempotency key;
      a retry is a new order. Reconcile via `/orders/live` first.
- [ ] **`external-identifier` is treated as a reconciliation tag**, never as a
      server-side de-duplication guarantee.
- [ ] **An unreadable reconciliation response escalates, never returns empty.**
      "Found nothing" and "could not tell" must not collapse into the same
      empty list.
- [ ] **Partial multi-leg fills are monitored.** A defined-risk spread that fills
      on one leg is a naked position.

## Environment and testing

- [ ] **Certification and production credentials are separate**, and certification
      is not treated as a mirror of production balances.
- [ ] **`Accept-Version` is sent on production only**; the sandbox rejects it.
- [ ] **Automated Testing:** run
      `python -m unittest discover -s skills/tastytrade-api-integration/scripts`
      — all tests pass.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
