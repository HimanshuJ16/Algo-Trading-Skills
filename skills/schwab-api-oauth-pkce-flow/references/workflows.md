# Workflows — Schwab Trader API OAuth

Endpoint, parameter and lifetime claims below are sourced in
`references/standards.md`. Schwab's documented flow is a **confidential-client
authorization-code flow**, not PKCE.

## 0. Decide whether unattended operation is actually possible

Before writing any code, settle this: **Schwab's refresh token expires 7 days after
creation, and nothing renews it programmatically.** A Schwab-connected bot needs a
human at a browser at least once a week, full stop. Design the operational calendar
around that (a fixed Sunday pre-market re-authorization is the common pattern)
rather than discovering it on a Wednesday afternoon.

If the strategy cannot tolerate a weekly human step, the correct outcome of this
skill is "pick a different broker for this strategy", not "engineer around it".
See `headless-broker-auth-patterns`.

## 1. Register the app and its callback

- Callback URLs must be HTTPS, including a loopback callback (`https://127.0.0.1`
  is explicitly allowed). Serving HTTPS on loopback needs a self-signed certificate
  on the local listener.
- The field is capped at 255 characters across all URLs listed.
- The value sent on the authorization request must match the registered value.
  A mismatch fails at Schwab's end with a security error, not a useful message.
- The app must reach Schwab's "Ready For Use" state before the endpoints respond.

## 2. Build the authorization URL

```
https://api.schwabapi.com/v1/oauth/authorize
    ?client_id={app_key}
    &redirect_uri={percent-encoded callback}
    &response_type=code
```

**Decision point — percent-encode the parameters.** A callback URL contains `:`,
`/` and often `?` or `&`. Interpolating it raw into a query string truncates it at
its own first separator, Schwab compares a mangled value against the registered
callback, and the login is rejected. Use a real URL encoder.

**Decision point — do not add PKCE parameters.** Schwab publishes no
`code_challenge` support. `SchwabOAuthManager.get_authorization_url` omits them
unless a caller passes a challenge explicitly, and logs a warning when one is
supplied because that path is unverified.

## 3. Capture and decode the authorization code

The user logs in, grants account access, and the browser is redirected to the
callback — landing on a 404 page, with the `code` in the address bar.

**Decision point — the code is percent-encoded and must be decoded before the
exchange.** Schwab's own documentation says so, and its example shows `%40`
becoming `@`. The widely copied community pattern — slicing the string between the
literals `code=` and `%40` — either truncates the trailing `@` or leaves the value
encoded, and the exchange then fails with an error that names nothing useful. Parse
the query string properly (`extract_code_from_callback` does).

Also check for an `error` parameter on the callback before looking for `code`: a
denied consent redirects with `error`, and treating that as "no code yet" produces
a hang rather than a clear failure.

## 4. Exchange the code for tokens

```
POST https://api.schwabapi.com/v1/oauth/token
Authorization: Basic {base64(app_key:app_secret)}
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code&code={decoded code}&redirect_uri={callback}
```

Response: `access_token`, `refresh_token`, `expires_in`, `token_type=Bearer`,
`scope=api`, `id_token`.

**Decision point — validate the response, don't default it.** If `expires_in` is
absent, fail. Assuming 1800 s means the client believes a dead token is live and
every subsequent call 401s for a reason nothing in the logs explains. If
`refresh_token` is absent, fail too — unattended operation is impossible without
it, and discovering that 30 minutes later is worse than discovering it now.

**Decision point — a lost response is ambiguous, not a failure.** The
authorization code is single-use. If the transport times out, Schwab may already
have consumed it; retrying with the same code will fail, and the recovery is a new
browser authorization, not a retry loop. `SchwabAmbiguousTokenError` marks this
case and leaves any existing token state untouched.

**Decision point — never put the response in an error message.** It carries three
credentials. Log `error`, `error_description` and the key names only.

## 5. Persist the tokens as the credentials they are

- Write to a temp file created with mode `0600` **before** any secret is written,
  `fsync` it, then `os.replace` onto the final path. Creating the temp file with
  default permissions leaves the tokens briefly world-readable; skipping `fsync`
  lets a crash leave a truncated file behind.
- **Decision point — a failed write must raise.** Logging and continuing leaves
  the operator believing the tokens will survive a restart. They will not, and the
  restart forces an unplanned manual re-login. `SchwabTokenPersistenceError` is
  raised while the in-memory state is retained, so the running process can keep
  trading and retry the write.
- **Decision point — one token owner per Schwab app.** The write is atomic but
  takes no cross-process lock. Two processes refreshing against the same file is
  last-writer-wins; if Schwab rotates the refresh token, the loser is left holding a
  stale value and the next refresh fails. Designate a single refresher and have
  other processes read the file.
- Keep the file out of the repository, out of container images, and out of backups
  that are less protected than the file itself. See
  `centralized-secrets-management-vault-integration` and
  `sandbox-credential-leakage-prevention`.

## 6. Refresh the access token on a buffer

```
POST https://api.schwabapi.com/v1/oauth/token
Authorization: Basic {base64(app_key:app_secret)}
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&refresh_token={stored refresh token}
```

**Decision point — refresh on a buffer, not on a 401.** The access token lives 30
minutes. Refreshing at 5 minutes remaining keeps a token-endpoint round trip off
the critical path of an order submission. Refreshing *after* a 401 puts it exactly
there, at the worst possible moment.

**Decision point — never move the 7-day deadline.** Refreshing does not extend the
refresh window. A client that re-anchors `refresh_expires_at` on each refresh will
never fire its warning and will die without notice. Anchor at the original
authorization and leave it there.

**Decision point — store a rotated refresh token if one comes back.** Schwab's
response includes a `refresh_token`. Whether it rotates is not documented; storing
whatever arrives is correct in both cases.

**Decision point — classify `invalid_client` as "re-authorize", not "retry".**
That is how Schwab rejects an over-age refresh token. Retrying cannot succeed;
escalate to a human. `SchwabRefreshTokenExpiredError` exists so an alert can be
wired to exactly this condition.

**Decision point — a refresh that fails in transport is ambiguous.** Schwab may
have rotated the refresh token before the response was lost. Do not overwrite
stored state on a transport failure; retry the refresh once with the stored token
and, if that also fails with `invalid_client`, treat it as a re-authorization
event.

## 7. Monitor the 7-day window

- Check `is_refresh_token_expiring_soon()` on a schedule (hourly is ample) and
  raise an operator alert, not a log line, at 24 hours remaining.
- Escalate the alert as the deadline approaches; the remedy needs a human at a
  browser and cannot be automated.
- Prefer re-authorizing during a planned window (before Sunday's pre-market) over
  waiting for the warning to fire mid-session. Re-authorizing early is free — a
  fresh authorization simply starts a new 7-day window.
- Feed the deadline into the same alerting path as other broker-connectivity
  health signals; see `token-lifecycle-live-probing` and
  `broker-status-page-monitoring-integration`.

## 8. Use the access token

Authenticated Trader API requests carry `Authorization: Bearer {access_token}`.
`get_bearer_header()` refuses to build the header when the token is missing or
inside the refresh buffer, so a stale token fails locally rather than as an opaque
401 mid-order.

Order requests (PUT/POST/DELETE) are subject to a per-account throttle
configurable from 0 to 120 requests per minute; see
`multi-broker-rate-limit-handling`.
