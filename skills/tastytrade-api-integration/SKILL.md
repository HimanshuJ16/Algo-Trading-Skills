---
name: tastytrade-api-integration
description: >-
  Use when building or migrating a Tastytrade options or futures bot. Session-token
  authentication was discontinued on 2025-12-01 in favour of an OAuth2 refresh-token
  grant, and equity options use the 21-character OCC symbol layout.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, tastytrade, tastyworks, oauth2, options-trading, multi-leg-orders, occ-symbology, order-reconciliation, futures
  brokers_frameworks: "Tastytrade API (api.tastyworks.com); OAuth 2.0 refresh-token grant (RFC 6749); OCC 21-character option symbology; tastyware/tastytrade (community reference client)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this when building an options- or futures-focused automated strategy on
Tastytrade, or when migrating an existing bot off the retired session-token
flow.

**Start with the authentication change, because most Tastytrade tooling and most
model training data predate it.** Tastytrade announced that "session-token
authentication will be discontinued on December 1st, 2025." The `POST /sessions`
call that traded an email and password for a `session-token`, and the
`Authorization: {session-token}` header that went with it, are gone. The
replacement is an OAuth2 refresh-token grant:

```
POST /oauth/token   {"grant_type": "refresh_token",
                     "client_secret": "...", "refresh_token": "..."}
->  {"access_token": "...", "token_type": "Bearer", "expires_in": 900}
Authorization: Bearer {access_token}
```

**And settle the token cadence before writing anything.** Tastytrade's own FAQ
states "Access tokens last 15 minutes and must be sent with every request in the
Authorization header." Not 24 hours, and not 24 hours of inactivity. Any client
that assumes a day-long session will 401 mid-session, and the natural place for
that to surface is the order submission you least want to retry. The refresh
token itself is long-lived, so unattended operation is possible — this is the
one broker in this repo's integration set where it is.

What this skill then covers: the required User-Agent, building OCC symbols that
cannot silently name the wrong contract, running the dry-run preflight, encoding
net price direction correctly, and deciding what to do when an order submission's
outcome is unknown.

## When NOT to Use

- **As a futures-option symbology reference.** Only *equity* options use the
  21-character OCC layout. Tastytrade future options carry their own format
  (`./ESU4 EW4Q4 240823C5750`), so `format_occ_symbol` must not be used to build
  them; resolve them from the future-option chain instead. The client accepts a
  `Future Option` leg but does not construct or validate its symbol.
- **For OCO/OTOCO bracket structures.** "Complex" here means multi-leg in one
  order. Tastytrade's separate `/complex-orders` endpoint groups several orders
  into contingent structures and is out of scope — see
  `conditional-order-logic-for-execution-triggers`.
- **For stop or stop-limit orders.** Only `Limit` and `Market` are modelled.
  Adding `Stop` requires the `stop-trigger` field, which this client does not
  send.
- **As an idempotency layer.** Tastytrade publishes no client-supplied
  idempotency key for order placement, so no wrapper here can make a retry safe.
  See `order-placement-idempotency` for the ledger pattern that has to sit above
  this client.
- **As a risk control.** Nothing here bounds exposure, drawdown, assignment risk
  or order rate. See `kill-switch-and-drawdown-circuit-breakers`,
  `sec-rule-15c3-5-risk-controls-us` and
  `early-exercise-assignment-risk-management`.
- **As a secrets manager.** The refresh token is a long-lived credential for the
  whole account. See `centralized-secrets-management-vault-integration`.
- **As an HTTP client.** Transport is injected deliberately, so timeout, TLS
  verification and retry policy stay with the caller — retry policy on an order
  submission is a risk decision this module must not make silently.

## Prerequisites

- A Tastytrade OAuth application with its **client secret**, plus a **refresh
  token** generated for the target account (OAuth Applications → Manage → Create
  Grant). A personal OAuth application is the path for unattended access.
- The scopes the strategy actually needs, selected when the application is
  created. Scopes cannot be widened on an existing grant.
- The target `account_number`, read from `/customers/me/accounts`.
- A `User-Agent` of the form `<product>/<version>`. Tastytrade's FAQ is explicit:
  "The format should be `<product>/<version>`, otherwise you'll get a 401" — and
  that 401 comes from the edge proxy, so it looks like an auth failure that no
  token change will fix.
- A caller-supplied `http_fn(method, url, headers, json_body) -> (status, body)`
  **that raises on transport failure** rather than synthesising a status code.
- Certification credentials for `api.cert.tastyworks.com` are separate from
  production credentials; the sandbox is not a mirror of production balances.

## Workflow

1. **Confirm which auth flow the target account is actually on.**
   - **Decision point:** if you find `POST /sessions`, `login`/`password`, a
     `session-token` field, or a bare `Authorization: {token}` header anywhere in
     the code you are extending, that code is written against the retired flow.
     Migrate it; do not extend it. `TastytradeClient.login()` raises
     `TastytradeAuthDiscontinuedError` specifically so this fails loudly rather
     than 401-ing opaquely.

2. **Acquire an access token, and never invent its lifetime.**
   - `POST /oauth/token` with `grant_type=refresh_token`. Send no `Authorization`
     header on this request.
   - **Decision point — a present-but-implausible `expires_in` is fatal, an
     absent one falls back to the documented 900s.** Trusting an oversized value
     leaves the client using a dead token; assuming a *shorter* lifetime than
     reality only causes a harmless early refresh. The asymmetry is the whole
     argument.
   - **Decision point — refresh at the buffer, not on a 401.** A 401 caught
     mid-order forces exactly the retry decision that has no safe answer.
     `ensure_access_token()` refreshes 60s before expiry so the token round trip
     is never on the order path.

3. **Resolve the contract, then build the symbol strictly.**
   - Resolve expirations and strikes from `/option-chains/{symbol}` rather than
     guessing; a strike you believe is listed may not be.
   - **Decision point — reject, never round.** `int(round(200.0001 * 1000))` is
     `200000`, so a naive formatter hands back the $200 strike to a caller who
     asked for something else, and every downstream check passes because the
     symbol is a well-formed 21 characters. `format_occ_symbol` rejects
     sub-1/1000 precision, roots over 6 characters, spelled-out `CALL`/`PUT`,
     impossible calendar dates and strikes that overflow the 8-digit field.
   - Use `parse_occ_symbol` to verify any symbol arriving from a chain, a config
     file or an upstream signal before trading it.

4. **Dry-run before every live submission.**
   - `POST /accounts/{acct}/orders/dry-run` returns the buying-power effect,
     projected fees and any warnings or errors without creating an order. It is
     the only preflight that reflects the account's real buying power.
   - **Decision point — a 2xx dry run can still carry `errors`.** Check
     `preview.is_acceptable`, not just the HTTP status.
   - A dry run is safe to retry. A live submission is not. That asymmetry is why
     they are separate methods here.

5. **Build the order payload with direction in `price-effect`.**
   - **Decision point — the API does not accept negative prices.** `price` is the
     magnitude; `Debit` (account pays) or `Credit` (account receives) carries the
     direction. If your strategy computes a signed net price, derive the effect
     with `price_effect_for_signed_price()` and send the absolute value.
   - **Decision point — a `Market` order has no price field.** Sending `price` or
     `price-effect` with one is malformed, and a caller who believes they sent a
     "market order with a limit price" has a much worse misunderstanding than a
     rejected request.
   - Set `external-identifier` to a per-attempt strategy tag. It is echoed back
     on the order, which makes step 7 possible.

6. **Submit, and classify the outcome into three buckets, not two.**
   - *Rejected* (4xx other than the indeterminate ones): no order exists, the
     payload must change, retrying it unchanged cannot help.
   - *Accepted*: a real `order.id` and a real `status` came back. Read
     `warnings` — a 2xx order response can carry them and they are not decorative.
   - *Ambiguous*: a transport exception, a 408/425/429/5xx, or a 2xx carrying no
     order id or no status. See step 7.

7. **On ambiguity, reconcile. Never retry.**
   - **Decision point — this is the one that costs real money.** Tastytrade
     publishes no client-supplied idempotency key for order placement, so a
     resubmission is a *new order*, not a deduplicated one. A timed-out four-leg
     iron condor that is retried becomes eight legs.
   - `GET /accounts/{acct}/orders/live`, filter on your `external-identifier`
     (`find_orders_by_external_identifier`), and only then decide. If the order
     is there, adopt it. If it is not, resubmit with a fresh tag.
   - **Decision point — "I found nothing" and "I could not tell" are different
     answers.** Both look like an empty list, and only one of them licenses a
     resubmission. On this path the client raises rather than returning `[]`
     when the response envelope is unreadable, or when no live order echoes an
     `external-identifier` at all.
   - `external-identifier` is a *reconciliation* tag. Tastytrade does not document
     server-side de-duplication on it, so it must not be relied on as an
     idempotency key.

8. **Track fills and positions out of band.**
   - `/accounts/{acct}/positions` for open exposure, `/accounts/{acct}/orders/live`
     for working orders. A multi-leg order can partially fill, leaving a naked
     leg where a defined-risk spread was intended.

> Full step-by-step procedure: see `references/workflows.md`.
> Sourced endpoints, headers and lifetimes: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Writing the `POST /sessions` password flow.** It was discontinued on
  2025-12-01. Most Tastytrade example code, and most model recall, still shows it.
- **Assuming a 24-hour session.** Access tokens last 15 minutes. A client built
  on the old assumption works for exactly one token's worth of trading and then
  401s, most visibly on whatever it was doing at minute 16.
- **Sending a default or missing User-Agent.** Tastytrade's edge proxy returns
  401 for anything that is not `<product>/<version>`, which reads as an auth
  failure and sends you debugging tokens that were never the problem.
- **Retrying an order submission after a timeout.** There is no idempotency key.
  The broker may already have the order; a retry doubles the position.
- **Trusting `external-identifier` as an idempotency key.** It is echoed back,
  not deduplicated. Use it to *find* the order, not to prevent the duplicate.
- **Fabricating an order id or status when the response omits one.** A synthetic
  id cannot cancel anything, and a synthetic `"Received"` hides a rejection.
  A 2xx with no id most likely means the order *is* live and merely unnamed.
- **Sending a negative price to signal a debit.** The API does not use negative
  numbers; direction lives in `price-effect`.
- **Sending `price` on a `Market` order.** Tastytrade's market order model has no
  price field.
- **Rounding a strike into the OCC field.** `round(200.0001 * 1000)` silently
  produces the $200 contract, and `round()` is banker's rounding, so
  `round(0.0005 * 1000)` is `0` — a symbol with an all-zero strike field.
- **Left-padding a root longer than 6 characters.** `ljust(6)` does not truncate,
  so the symbol comes out 22 characters and fails with an error naming nothing.
- **Using OCC symbology for future options.** They use `./ESU4 EW4Q4 240823C5750`,
  not the 21-character layout.
- **Discarding `warnings` on an accepted order.** They arrive on 2xx responses
  and can be the only signal that the fill will be poor.
- **Interpolating a raw response body into an error or log line.** The OAuth2
  token response travels the same code path; one careless f-string ships a live
  access token to the log aggregator.
- **Hammering the login endpoint on failure.** Tastytrade blocks the source IP
  outright after too many failed login attempts, typically for 8 hours, during
  which every request times out — including the ones managing open positions.
- **Treating certification as a production mirror.** It has separate credentials,
  separate balances, and rejects the `Accept-Version` header production expects.
- **Assuming a multi-leg order fills atomically across legs.** It can partially
  fill; a defined-risk spread can become a naked short leg.

## Verification

- **Retired flow:** `login()` raises `TastytradeAuthDiscontinuedError` naming the
  OAuth2 migration path.
- **Token request shape:** `POST /oauth/token` carrying exactly
  `grant_type`/`client_secret`/`refresh_token`, with the `<product>/<version>`
  User-Agent and no `Authorization` header; `Accept-Version` present on
  production and absent on certification.
- **Token validation:** `expires_in` of `900` sets expiry at `now + 900`; an
  absent one falls back to the documented 900s; `0`, `-5`, `"soon"`, `True`,
  `[900]` and anything over 86400 each raise; a missing, blank or non-string
  `access_token` raises; a non-object body raises.
- **Refresh lifecycle:** a live token is reused with no second token call; the
  buffer boundary flips at exactly `expiry - 60 s`; an unauthenticated client
  raises rather than building a header.
- **Secret hygiene:** a rejected grant whose body carries a `refresh_token`
  produces a message containing the OAuth error code and not the credential;
  `repr()` of credentials and of a session shows neither token.
- **User-Agent:** `python-requests`, `my bot/1.0`, `/1.0`, `bot/` and `""` each
  raise at construction; `mybot/1.2.3` is accepted.
- **OCC construction:** the published layout reproduces exactly
  (`AAPL  240816C00200000`, `SPY   241220P00500500`, `GOOGL 260116C00005000`);
  every root length from 1 to 6 yields 21 characters; a 7-character root,
  `CALL`, `2024-08-16`, `240230`, a strike of `100000`, `0`, `-1`, `200.0001`,
  `0.0005`, `NaN` and `inf` each raise; `99999.999` and the leap day `240229`
  succeed.
- **OCC parsing:** round-trips against the formatter across root lengths and
  strike magnitudes; 20- and 22-character symbols, a lowercase root and an
  impossible expiration each raise.
- **Order payload:** legs supplied as a generator still serialise as two legs
  rather than an empty array; a vertical spread produces exactly the documented
  `order-type`/`time-in-force`/`price`/`price-effect`/`legs` shape; an iron
  condor sends four legs; a `Market` order carries no price fields and one built
  with them raises before any network call; a `Limit` order without price or
  effect raises; a negative price raises and names the absolute value; price is
  serialised as an exact decimal string, never a float.
- **Leg validation:** a malformed OCC symbol, a zero or negative quantity, a
  fractional contract count, a non-finite quantity, an unknown instrument type
  and a non-`LegAction` action each raise; a future-option leg is not forced
  through OCC validation; duplicate `(symbol, action)` legs raise while the same
  symbol with opposite actions is allowed.
- **Outcome classification:** 400/401/403/404/422 raise
  `TastytradeOrderRejectedError` carrying the parsed error codes;
  408/425/429/5xx and a transport exception raise
  `TastytradeAmbiguousOrderError` carrying the account and external identifier;
  a 2xx with no order id and a 2xx with no status both raise ambiguous rather
  than fabricating `TT_ORD_1001`/`Received`; `warnings` on an accepted order
  reach the caller.
- **Dry run:** targets `/orders/dry-run` with the same payload, surfaces
  `buying-power-effect` and `fee-calculation`, and a 2xx carrying `errors`
  reports `is_acceptable == False`.
- **Reconciliation:** `/orders/live` unwraps the `data.items` envelope;
  `find_orders_by_external_identifier` matches the tagged order and returns
  empty for an unknown tag that *is* echoed; an unreadable `/orders/live`
  envelope and a live-order list echoing no `external-identifier` at all each
  raise rather than reading as "no orders"; position reads stay lenient and
  yield `[]`; a path-traversal account number raises before any request.
- Run `python -m unittest discover -s skills/tastytrade-api-integration/scripts`
  and confirm all tests pass.

## Related Skills

- `order-placement-idempotency`
- `broker-api-deprecation-notice-monitoring`
- `broker-api-versioning-migration-playbook`
- `headless-broker-auth-patterns`
- `token-lifecycle-live-probing`
- `sandbox-vs-production-endpoint-drift`
- `calendar-spread-and-multi-leg-order-atomicity`
- `options-margin-span-calculation-global`
- `early-exercise-assignment-risk-management`
- `broker-agnostic-adapter-interface`
- `paper-to-live-promotion-checklist`
- `multi-broker-rate-limit-handling`
