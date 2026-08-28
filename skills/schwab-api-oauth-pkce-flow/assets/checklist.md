# Pre-Flight Checklist — Schwab Trader API OAuth

## Before you build

- [ ] Has the team accepted that a **human must re-authorize at a browser every 7
      days**? Schwab's refresh token expires 7 days after creation with no
      programmatic renewal. If that is unacceptable, choose a different broker for
      this strategy.
- [ ] Is a re-authorization slot scheduled in a planned window (e.g. Sunday
      pre-market) rather than left to fire as an alert mid-week?
- [ ] Is the flow implemented as Schwab documents it — authorization code plus HTTP
      Basic client authentication — and **not** as PKCE? Schwab publishes no
      `code_challenge` support.

## App registration

- [ ] Is the callback URL HTTPS (loopback `https://127.0.0.1` is allowed)?
- [ ] Is the callback under Schwab's 255-character field limit?
- [ ] Does the `redirect_uri` sent on the request match the registered value exactly?
- [ ] Is the app in Schwab's "Ready For Use" state?

## Authorization request

- [ ] Are `client_id` and `redirect_uri` percent-encoded rather than string-interpolated?
- [ ] Are PKCE parameters absent unless deliberately, knowingly enabled?

## Code capture and exchange

- [ ] Is the callback's `code` extracted with a real query-string parser and
      percent-**decoded** (Schwab codes end in `%40` → `@`)?
- [ ] Is an `error` parameter on the callback detected and surfaced?
- [ ] Is the Basic header built from `base64(app_key:app_secret)` with a
      `Content-Type: application/x-www-form-urlencoded` body?
- [ ] Does a missing `expires_in` fail loudly instead of defaulting to 1800 s?
- [ ] Does a missing `refresh_token` fail loudly?
- [ ] Is a transport timeout treated as **ambiguous** (the single-use code may be
      spent) rather than retried with the same code?

## Token storage

- [ ] Is the token file written atomically — temp file created at mode `0600`
      before any secret is written, `fsync`, then `os.replace`?
- [ ] Is the final file owner-readable only (`0600`)?
- [ ] Does a failed write **raise** rather than log-and-continue?
- [ ] Is the token file excluded from version control, images and unprotected backups?
- [ ] Is exactly **one** process designated as the token refresher? The write takes
      no cross-process lock, so concurrent refreshes are last-writer-wins.

## Refresh lifecycle

- [ ] Is the access token refreshed on a buffer (default 300 s) rather than in
      response to a 401?
- [ ] Is `refresh_expires_at` anchored at the **original** authorization and never
      pushed forward by a refresh?
- [ ] Is a rotated `refresh_token` stored when one is returned?
- [ ] Is `invalid_client` classified as "human re-authorization required" rather
      than retried?
- [ ] Does a transport failure during refresh preserve the stored token state?

## Monitoring and secret hygiene

- [ ] Does an operator **alert** (not just a log line) fire at 24 hours remaining
      on the refresh window?
- [ ] Are `access_token`, `refresh_token` and `id_token` excluded from exception
      messages and from `repr`/log output?
- [ ] Is the app secret sourced from a secrets manager rather than a config file
      checked into the repo?

## Verification

- [ ] `python -m unittest discover -s skills/schwab-api-oauth-pkce-flow/scripts` passes.
