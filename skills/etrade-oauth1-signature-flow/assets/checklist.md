# Pre-Flight Checklist — etrade-oauth1-signature-flow

## Credentials

- [ ] Consumer key and secret stored in environment/vault, never in source or logs.
- [ ] Sandbox and production key pairs kept separate; `use_sandbox` set deliberately.
- [ ] Credential objects never logged raw — secret fields are `repr=False`; confirm no
      `print`/`f"{creds}"` reintroduces them.

## Signature correctness

- [ ] Base string reproduces the RFC 5849 §3.4.1.1 published vector.
- [ ] HMAC-SHA1 reproduces the X OAuth 1.0a published signature.
- [ ] Query parameters participate in the signature; the base string URI carries no query
      or fragment.
- [ ] Scheme and host lowercased; default port stripped.
- [ ] Parameters percent-encoded **before** sorting.
- [ ] `oauth_signature` and `realm` excluded from the base string.
- [ ] Signing key retains its trailing `&` when there is no token secret.
- [ ] `oauth_signature` percent-encoded inside the `Authorization` header value.

## Three-legged flow

- [ ] Request-token call sends `oauth_callback="oob"`.
- [ ] All four token endpoints called with **GET**.
- [ ] Authorize URL built from `https://us.etrade.com/e/t/etws/authorize` — not the API
      host — in both sandbox and production.
- [ ] Consumer key and request token percent-encoded into the authorize query.
- [ ] Request token treated as dead after 5 minutes (restart at leg one, no retry).
- [ ] Token responses rejected unless both `oauth_token` and `oauth_token_secret` are
      present; error bodies never stored as credentials.

## Token lifecycle

- [ ] 2-hour idle inactivation handled via `renew_access_token`, with a keepalive or a
      renew-on-401-after-quiet path — not assumed away by "renew before market open".
- [ ] End-of-calendar-day US Eastern expiry handled by re-running the full flow; renew
      loops against an expired token are bounded, not infinite.
- [ ] 401s classified (idle vs expired vs bad signature) before any retry.
- [ ] Access token revoked on shutdown or suspected compromise.

## Environment

- [ ] Host clock synchronized to within ±5 minutes; drift monitored and alerted.
- [ ] Nonce generated fresh per request; signed headers never cached or replayed.

## Tests

- [ ] Run `python scripts/test_etrade_auth.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
