---
name: etrade-oauth1-signature-flow
description: >-
  Use when integrating E*TRADE, which signs every request with OAuth 1.0a HMAC-SHA1
  rather than OAuth 2.0 bearer tokens. Covers the three-legged flow with
  oauth_callback=oob, RFC 5849 signature base strings, and the idle and end-of-day
  expiry rules.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, etrade, oauth1, hmac-sha1, request-signing, rfc-5849
  brokers_frameworks: "E*TRADE; OAuth 1.0a (RFC 5849); HMAC-SHA1"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when building a trading bot for E\*TRADE. Unlike most modern broker APIs
that use OAuth 2.0 bearer tokens, E\*TRADE uses **OAuth 1.0a** with HMAC-SHA1 request
signing. Every API request must carry an `Authorization: OAuth ...` header with a nonce,
timestamp, and signature computed over a canonical *signature base string*. This skill
covers the full three-legged flow, per-request signing, and E\*TRADE's two distinct token
lifetime rules.

## When NOT to Use

- **For any other broker.** OAuth 1.0a request signing is specific to E\*TRADE among the
  brokers covered here. Schwab uses OAuth 2.0 PKCE (`schwab-api-oauth-pkce-flow`), Upstox
  uses refresh-token rotation (`upstox-oauth-refresh-token-rotation`).
- **As a general OAuth 1.0a library.** The signing core here is RFC 5849-conformant, but
  `RSA-SHA1` and `PLAINTEXT` are not implemented — E\*TRADE documents `HMAC-SHA1` as the
  only supported `oauth_signature_method`.
- **As an HTTP client.** `scripts/etrade_auth.py` deliberately issues no requests and adds
  no dependencies. It returns URLs and header values; you supply the transport, timeouts,
  and retry policy.
- **As a headless/unattended login.** Leg two requires a human to approve in a browser and
  copy back a verifier code. Automating a daily unattended restart is a separate problem —
  see `headless-broker-auth-patterns` and `token-lifecycle-live-probing`.
- **As secret storage.** This holds credentials in memory only. Persistence, rotation, and
  vaulting belong to `centralized-secrets-management-vault-integration` and
  `secrets-rotation-without-bot-downtime`.

## Prerequisites

- E\*TRADE developer account with a consumer key and consumer secret. Sandbox and
  production issue **separate** key pairs.
- A browser-capable human for leg two of the flow, once per calendar day.
- Host clock synchronized to within **5 minutes** of E\*TRADE's clock (`oauth_timestamp`
  tolerance). See `clock-drift-monitoring-alerting-thresholds`.

## Workflow

1. **Request Token** — `GET {base_url}/oauth/request_token`.
   - All four token endpoints are **GET**, not POST.
   - **`oauth_callback` is mandatory and must be `"oob"`**, whether or not the app has a
     callback URL registered. It has no server-side default; omitting it fails the call.
     `build_request_token_header()` sets it.
   - Sign with the consumer secret and an **empty token secret** — the signing key still
     ends in `&`.
   - Parse the form-encoded response with `parse_token_response()`. An E\*TRADE error body
     also parses cleanly as form data, so **decision point: reject any response without
     both `oauth_token` and `oauth_token_secret` rather than storing empty strings** — an
     empty-credential client fails opaquely at the first API call, not here.

2. **User Authorization** — `GET https://us.etrade.com/e/t/etws/authorize?key=…&token=…`.
   - **Decision point — this is a different host.** The authorize page is on
     `us.etrade.com` for **both** sandbox and production. Deriving it from `api.etrade.com`
     or `apisb.etrade.com` yields a dead URL.
   - Percent-encode both query values. Request tokens routinely contain `+`, `/`, and `=`;
     an unencoded `+` arrives at the server as a space and authorizes nothing.
   - The user approves and receives an `oauth_verifier` on the Authorization Complete page
     (or appended to a registered callback URL).
   - **The request token is valid for 5 minutes.** If the user is slower, restart at leg
     one — do not retry the exchange with the stale token.

3. **Access Token** — `GET {base_url}/oauth/access_token`.
   - Sign with the consumer secret **and the request token secret**, including
     `oauth_token` (the request token) and `oauth_verifier`.

4. **Sign Requests** — build the RFC 5849 base string, then HMAC-SHA1 it.
   - Base string URI (§3.4.1.2): lowercase scheme and host, drop the default port, and
     **exclude the query and fragment**.
   - Parameters (§3.4.1.3.1): the `oauth_*` header parameters **plus the URL's query
     parameters plus any form-encoded body parameters**. `oauth_signature` and `realm` are
     excluded.
   - Normalization (§3.4.1.3.2): percent-encode **first**, then sort by encoded name and,
     for repeated names, by encoded value.
   - Signing key: `percent_encode(consumer_secret) + "&" + percent_encode(token_secret)`.
   - **Decision point — sign the exact URL you will send.** Signing a bare path and then
     appending `?detailFlag=ALL` produces a signature E\*TRADE rejects, because the query
     parameters were never in the base string.

5. **Token Lifecycle** — two independent rules, with different remedies.
   - **Idle inactivation:** after **2 hours** with no API request the access token is
     inactivated. Remedy: `GET {base_url}/oauth/renew_access_token`
     (`sign_renew_access_token()`).
   - **Daily expiry:** the token expires at the **end of the current calendar day, US
     Eastern**. Remedy: the full three-legged flow again, with a human. Renewal does not
     extend past midnight ET.
   - **Decision point — classify the failure before reacting.** A 401 after a quiet period
     is an idle inactivation and needs a renew; a 401 after midnight ET needs
     re-authorization. Looping renew calls against an expired token never recovers.
   - Revoke on shutdown or suspected compromise: `GET {base_url}/oauth/revoke_access_token`
     (`sign_revoke_access_token()`).

> Full procedure: see `references/workflows.md`.
> Standards and endpoint table: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Omitting query parameters from the signature base string.** The single most common
  cause of a persistent 401 on E\*TRADE: `/v1/accounts/{id}/orders` signs fine, then
  `?status=OPEN&count=50` is appended and every call is rejected. Query parameters are a
  mandatory signature input (RFC 5849 §3.4.1.3.1), and the base string URI must carry no
  query at all.
- **Sorting parameters before percent-encoding.** Raw `"a b" < "a+"` (0x20 < 0x2B), but
  encoded `"a%20b" < "a%2B"` orders them the other way. The difference only surfaces on
  values containing spaces, `+`, or non-ASCII — so it passes every simple test and fails on
  the first symbol list or free-text order field.
- **Omitting `oauth_callback` on leg one.** E\*TRADE requires it on the request-token call
  and documents that it must always be `"oob"`. There is no default.
- **Building the authorize URL from the API host.** The authorize page is on
  `us.etrade.com`, not `api.etrade.com`/`apisb.etrade.com`, in both environments.
- **Not percent-encoding the request token in the authorize URL.** Base64-ish tokens
  contain `+`, `/`, and `=`; an unencoded `+` is decoded server-side as a space.
- **Not percent-encoding the signature in the header.** Base64 output contains `+`, `/`,
  and `=`, all of which must be escaped inside the quoted header value.
- **Renewing "before market open" and assuming you are covered.** The 2-hour idle clock is
  independent of the trading session. A strategy that places one order at the open and then
  goes quiet is inactivated by 11:30 ET even though the token has not expired.
- **Retrying a renew against a token that expired at midnight ET.** Renewal only reverses
  idle inactivation. Past end-of-day US Eastern the token is gone and needs a human.
- **Reusing a nonce with the same timestamp.** E\*TRADE rejects it. Generate the nonce and
  timestamp fresh per request; never cache a signed header for reuse.
- **Clock skew.** `oauth_timestamp` must be within 5 minutes of E\*TRADE's clock. A drifting
  container clock produces signature rejections that look like credential failures.
- **Logging credential objects.** A default dataclass `repr` prints secrets in plain text
  into logs and tracebacks. `OAuth1Credentials` and `OAuth1Token` mark secret fields
  `repr=False`; keep it that way.

## Verification

- Reproduce the **RFC 5849 §3.4.1.1** published base string with
  `build_base_string("POST", "http://example.com/request?b5=%3D%253D&a3=a&c%40=&a2=r%20b", …)`
  including the body parameters `c2=` and `a3=2 q` and a `realm` that must be dropped. This
  vector exercises query-parameter inclusion, a repeated name, `realm` exclusion, and
  encode-then-sort ordering at once.
- Reproduce the **X (Twitter) OAuth 1.0a documented signature** `Ls93hJiZbQ3akF3HF3x1Bz8/zU4=`
  from its published base string and signing key, which pins HMAC-SHA1 and base64 end to end.
- Confirm `build_request_token_header()` emits `oauth_callback="oob"` and no `oauth_token`.
- Confirm `get_authorize_url()` starts with `https://us.etrade.com/e/t/etws/authorize?` for
  both `use_sandbox=True` and `False`, and that a token `a+b/c=` encodes to `a%2Bb%2Fc%3D`.
- Negative checks: `parse_token_response()` must raise on an empty body, on
  `oauth_problem=signature_invalid`, and on a response missing either token field;
  `sign_request()` and `sign_renew_access_token()` must raise before an access token is set;
  blank consumer credentials must raise at construction.
- Confirm `repr(OAuth1Credentials(...))` contains neither secret.
- Run `python -m unittest discover -s skills/etrade-oauth1-signature-flow/scripts` and confirm 100% pass rate.

## Related Skills

- `headless-broker-auth-patterns`
- `schwab-api-oauth-pkce-flow`
- `broker-agnostic-adapter-interface`
- `token-lifecycle-live-probing`
- `clock-drift-monitoring-alerting-thresholds`
