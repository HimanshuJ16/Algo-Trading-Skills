# Standards Reference — etrade-oauth1-signature-flow

## Protocol

| Parameter | Value | Source |
|---|---|---|
| Auth protocol | OAuth 1.0a, three-legged | RFC 5849 |
| `oauth_signature_method` | `HMAC-SHA1` — the only value E\*TRADE supports | E\*TRADE Authorization API |
| `oauth_version` | `1.0` | RFC 5849 §3.1 |
| `oauth_timestamp` tolerance | ±5 minutes of E\*TRADE's clock | E\*TRADE Authorization API |
| `oauth_nonce` | Single-use per timestamp | E\*TRADE Authorization API |
| `oauth_callback` | **Must always be `oob`** on the request-token call | E\*TRADE Request Token API |
| Percent-encoding unreserved set | `ALPHA` / `DIGIT` / `-` / `.` / `_` / `~` | RFC 5849 §3.6, RFC 3986 §2.3 |

## Endpoints

All four token endpoints are **GET**. `{base}` is `https://api.etrade.com` (production) or
`https://apisb.etrade.com` (sandbox).

| Step | Method | URL |
|---|---|---|
| 1. Request token | GET | `{base}/oauth/request_token` |
| 2. Authorize | GET | `https://us.etrade.com/e/t/etws/authorize?key={consumer_key}&token={request_token}` |
| 3. Access token | GET | `{base}/oauth/access_token` |
| Renew (idle) | GET | `{base}/oauth/renew_access_token` |
| Revoke | GET | `{base}/oauth/revoke_access_token` |
| API resources | varies | `{base}/v1/...` |

The authorization URL is on `us.etrade.com` for **both** sandbox and production; only the
API/token host differs between environments.

## Token lifetimes

| Token | Rule | Remedy |
|---|---|---|
| Request token | Valid 5 minutes | Restart at leg one |
| Access token — idle | Inactivated after 2 hours with no API request | `renew_access_token` |
| Access token — expiry | Expires at end of the current calendar day, US Eastern | Full three-legged flow (human required) |

Renewal reverses idle inactivation only; it does not extend a token past midnight ET.

## Signature base string (RFC 5849 §3.4.1)

1. **Base string URI** (§3.4.1.2) — lowercase scheme and host; include the port only when
   it is not the scheme default; **exclude query and fragment**.
2. **Parameter sources** (§3.4.1.3.1) — the URI query component, the `Authorization` header
   `oauth_*` parameters (excluding `realm`), and a single-part
   `application/x-www-form-urlencoded` entity body. `oauth_signature` is excluded.
3. **Normalization** (§3.4.1.3.2) — percent-encode each name and value, **then** sort by
   encoded name and, for repeated names, by encoded value; join as `name=value` with `&`.
4. **Concatenation** — `UPPERCASE_METHOD & pct(base_string_uri) & pct(normalized_params)`.
5. **Signing key** (§3.4.2) — `pct(consumer_secret) + "&" + pct(token_secret)`; the
   trailing `&` is present even when there is no token secret.

## Test vectors used by this skill

| Vector | Purpose | Source |
|---|---|---|
| `POST&http%3A%2F%2Fexample.com%2Frequest&a2%3Dr%2520b%26a3%3D2%2520q%26a3%3Da%26b5%3D%253D%25253D%26c%2540%3D%26c2%3D%26oauth_consumer_key%3D9djdj82h48djs9d2%26…` | Base string: query params, repeated name, `realm` exclusion, encode-then-sort | RFC 5849 §3.4.1.1 |
| `oauth_signature = Ls93hJiZbQ3akF3HF3x1Bz8/zU4=` | HMAC-SHA1 + base64 end to end | X (Twitter) OAuth 1.0a "Creating a signature" |

## References

- RFC 5849, *The OAuth 1.0 Protocol* (April 2010) — §3.4 Signature, §3.6 Percent Encoding.
  https://www.rfc-editor.org/rfc/rfc5849.txt
- RFC 3986, *URI Generic Syntax* — §2.3 Unreserved Characters.
- E\*TRADE Developer Platform, Authorization APIs — Request Token, Authorize, Get Access
  Token, Renew Access Token, Revoke Access Token.
  https://apisb.etrade.com/docs/api/authorization/request_token.html
- X (Twitter) Developer Platform, *OAuth 1.0a — Creating a signature*.
  https://docs.x.com/resources/fundamentals/authentication/oauth-1-0a/creating-a-signature
