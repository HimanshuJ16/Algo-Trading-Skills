# Deep Workflow Reference — questrade-api-rate-limit-and-account-types

Full technical procedure referenced by `SKILL.md`. Every documented figure is
sourced in `references/standards.md`; anything labelled **Inferred** there is
this skill's judgement rather than published Questrade behaviour.

## 0. Confirm your access tier before designing anything

Questrade's `trade` scope — `POST accounts/:id/orders`, `POST …/impact`,
`DELETE …/orders/:orderId` — is documented as **"partner developers only"**. A
personal API application is issued `read_acc` and `read_md`, and no
order-placement endpoint appears in the public REST reference.

Consequences for design:

- A personal app can read accounts, positions, balances, executions, orders and
  market data, and can stream order/execution notifications.
- It cannot submit, replace or cancel orders. If your architecture assumed it
  could, resolve that with Questrade before building the execution path.
- The eligibility gate in `scripts/questrade_client.py` is therefore a
  *pre-submission check* for whatever component holds trade access — a partner
  integration, or a human at a terminal.

## 1. OAuth2 refresh-token exchange

1. Select the host: `https://login.questrade.com` for live accounts,
   `https://practicelogin.questrade.com` for practice accounts. They are not
   interchangeable.
2. Percent-encode the refresh token before placing it in the query string.
   Questrade's documented sample tokens include `p4VTj45GhS8lY7aFoKDNZxB8yQHMOr+f`
   and `C3lTUKuNQrAAmSD/TPjuV/HI7aNrAwDp`. A literal `+` in a query string
   decodes to a space, so the server receives a token that is not the one you
   hold — and the failure looks like an invalid token, not an encoding bug.
3. Issue `grant_type=refresh_token&refresh_token={ENCODED}` against
   `/oauth2/token`. Questrade documents this both as a bare URL (GET, in Getting
   started) and as `POST /oauth2/token` (in Security). `QuestradeClient` defaults
   to GET for continuity and exposes `token_request_method="POST"` for
   deployments that prefer to keep the secret out of a URL — request URLs are
   routinely captured by proxies and access logs.
4. Validate the response before trusting it. Require `access_token`,
   `refresh_token`, `api_server` **and** `expires_in`.
   - Never default `expires_in`. Questrade's samples show 300 s (Authorization,
     Getting started) and 1800 s (Implicit, Security). Guessing 1800 against an
     actual 300 leaves the client confident about a token that expired 25
     minutes earlier, and every subsequent call 401s.
   - Reject `expires_in <= 0` rather than installing an already-dead session.
5. Persist the rotated `refresh_token` **inside** the exchange, before returning.
   `token_persist_fn` is invoked before `refresh_access_token` returns and a
   raising callback is converted into a fatal `QuestradeAuthError` with no
   session installed. The reasoning: the submitted token was consumed the moment
   Questrade answered, so a process that continues without durably recording the
   successor has one process lifetime of access left and no recovery path.
6. Record expiry against a **monotonic** deadline as well as wall clock. Wall
   clock can step on an NTP correction; `expires_at` is retained for display and
   persistence, `monotonic_deadline` drives the decisions.

### The 7-day cliff

A manual-authorization token "expires in 7 days from the date and time the token
is generated" (Getting started). Practical consequences:

- Rotation is not merely hygiene — it is what keeps access alive. A bot that
  does not redeem within 7 days is locked out.
- A deployment freeze, an extended holiday close, or a crashed rotation loop can
  all consume that window silently.
- Recovery is manual: log in, API Centre → Personal applications → generate a
  new token. Questrade does not store the token decrypted, so there is nothing
  to recover programmatically.
- Alert on `time since last successful rotation`, not just on call failures.
  See `secrets-rotation-without-bot-downtime` and
  `token-lifecycle-live-probing`.

## 2. Normalise `api_server`, then build URLs

Questrade returns this field per session, and its own documentation shows three
different shapes (see `references/standards.md`). `normalize_api_server()`:

1. Rejects a non-string, blank, or non-`https://` value. Questrade refuses
   plaintext connections, so a `http://` value indicates a tampered or misparsed
   response rather than a usable endpoint.
2. Strips trailing slashes.
3. Strips a trailing `/v1` version segment.
4. Appends exactly one `/`.

The result is a base that joins cleanly with version-qualified paths
(`v1/accounts`), so the caller keeps writing paths the way the reference
documents them.

## 3. Rate limiting

### The two categories

| Category | Per second | Per hour |
|---|---|---|
| Account calls | 30 | 30,000 |
| Market Data calls | 20 | 15,000 |

Both windows in a category bind simultaneously and are consumed
**all-or-nothing**: `MultiWindowBudget.acquire()` first checks that *every*
window would admit the request, and only then charges them. Sequential
consumption would charge the per-second window on attempts the hourly window
then refuses, leaking capacity from the fast window on every rejection.

### Why a sliding-window counter, not a token bucket

Questrade states its limits as request *counts per window* ("maximum allowed
requests per second", "maximum allowed requests per hour"). A token bucket of
capacity `N` refilling at `N/period` starts full and refills continuously, so
over the first period it grants roughly `2N`. Sized to the 30,000/hour cap, that
is ~60,000 requests in the first hour — double the limit.

`SlidingWindowCounter` keeps grant timestamps instead, which makes the window
exact and bounds memory at `capacity` entries (30,000 floats for the largest
budget). `TokenBucketRateLimiter` remains in the module because rate *smoothing*
is genuinely useful — pacing a historical backfill comfortably below a cap, for
instance — but it is not what enforces the caps.

### Categorising an endpoint

`categorize_endpoint()` normalises the path (stripping the `v1` prefix and
replacing id segments) and looks it up in the documented rate-limit table.

Two traps:

- **Scope is not category.** `GET markets`, `GET symbols/:id` and
  `GET symbols/:id/options` belong to the `read_acc` *scope* but are metered as
  *Market Data* calls. Categorising from the scope table sends them against the
  wrong budget.
- **The table is incomplete.** `accounts/:id/activities`, `symbols/search`,
  `markets/quotes/options` and `markets/quotes/strategies` exist in the REST
  reference but appear in neither category. They fall through to the tighter
  Market Data budget. This is **Inferred**: over-throttling costs latency,
  under-throttling costs a 429 and eventually a suspension.

### Waiting, and refusing to wait

`QuestradeClient._await_budget()` waits up to `max_wait_sec` (default 5 s) for
the category budget, then raises `QuestradeRateLimitError(source="local")`
naming the binding window. A deadline matters: a spin loop against an exhausted
hourly budget would block the calling thread for up to an hour. A poll whose
answer is no longer timely should surface as an error to reconciliation rather
than park a worker.

`source="local"` carries a guarantee worth relying on: **no request was
dispatched**, so nothing can have executed broker-side. `source="server"` (an
actual 429) carries no such guarantee for a mutating call.

### Server feedback

Questrade returns `X-RateLimit-Remaining` and `X-RateLimit-Reset` on every
limited response and on the 429 itself.

- `apply_headers()` matches header names case-insensitively and ignores a
  malformed value rather than corrupting the budget.
- `resync()` only ever **reduces** headroom. A server figure higher than the
  local estimate usually means the server is counting traffic from another
  process on the same API key; trusting it upward lets both processes believe
  they hold the full quota. Synthetic charges are stamped at "now", so they age
  out over a full period — deliberately conservative.
- `resync()` applies the header to the **shortest-period window only**, and
  ignores a value exceeding that window's capacity. Questrade sends a single
  `X-RateLimit-Remaining` and does not state which window it describes; applying
  it to all of them means `remaining: 29` charges 29,971 requests against the
  30,000/hour budget and strands the client for an hour on one ambiguous header.
  The short window is the one a burst actually hits and the only one whose
  capacity is commensurate with a small reading. **Inferred** — the header is
  documented, its scope is not.
- A 429 raises `QuestradeRateLimitError` with the parsed `reset_at`. For
  `Retry-After`-aware full-jitter backoff on top of this, see
  `multi-broker-rate-limit-handling`; do not compute a retry delay shorter than
  the broker's stated reset.

### Classification

Classify a throttle on the numeric HTTP status, never on message text.
`"429" in str(exc)` also matches order id `429123` and limit price `429.50`; a
false positive retries a call the broker may already have executed. The client
raises `QuestradeAPIError` (not `QuestradeRateLimitError`) for an HTTP 400 whose
body reads `"Order 429123 rejected"`, and there is a regression test for exactly
that string.

## 4. Account registry

`fetch_accounts()` reads `GET v1/accounts` and rebuilds the registry rather than
merging into it, so an account closed or removed since the previous call does not
linger as `Active`.

Per record:

- `number` is required; a record without one raises rather than being skipped.
  Questrade documents it as an eight-digit string — keep it a string, never coerce
  to int (leading zeros).
- `type` maps through `AccountType.from_api()`. **An unrecognised value becomes
  `UNKNOWN`, never `MARGIN`.** The old fallback to `MARGIN` was the single most
  dangerous line in this skill: `LIRA`, `RESP`, `RRIF`, `Cash` and every future
  account type all became "margin", and then passed the short-selling check.
  `strict_account_types=True` raises instead, for deployments that would rather
  fail loudly than operate in a degraded mode.
- `status` maps through `AccountStatus.from_api()`; an unrecognised value becomes
  `UNKNOWN` and denies trading.
- `clientAccountType` is stored separately. `Individual`, `Joint`, `Corporation`
  and friends live in this field — `Individual` is **not** an account type, and
  conflating the two loses the actual plan type.

## 5. Pre-trade eligibility

`check_order_eligibility(account_number, order_side)` returns an
`OrderEligibility` with one of three outcomes.

Order of evaluation:

1. **Order side must be documented.** `Buy`, `Sell`, `Short`, `Cov`, `BTO`,
   `STC`, `STO`, `BTC`. Anything else — including the previously used
   `"SellShort"`, which is not a Questrade value — raises `ValueError`, because
   an unrecognised side is a programming error and silently denying it would hide
   the bug.
2. **Account status.** `Liquidate Only` permits only position-reducing sides
   (`Sell`, `Cov`, `STC`, `BTC`); any other non-`Active` status, including
   `UNKNOWN`, denies everything. (**Inferred** from the status names —
   Questrade's enumeration table gives no descriptions.)
3. **Borrow-requiring sides.** `Short` and `Cov` are permitted only where
   `can_borrow` is true, which is `Margin` alone. The denial reason distinguishes
   the three cases — registered plan, unregistered-but-no-margin (`Cash`), and
   unrecognised type — because an operator reading the log needs to know which.
4. **Option writing.** `STO` outside a `Margin` account returns
   `REVIEW_REQUIRED`. Whether a *covered* write is permitted inside a given
   registered plan depends on Questrade account approvals the API does not
   report, so the honest answer is neither "allowed" nor "denied". `.allowed`
   folds `REVIEW_REQUIRED` to `False`, so a caller reading only the boolean fails
   closed.

Three call styles, deliberately:

- `check_order_eligibility()` — full result, use when the reason matters.
- `validate_order_for_account()` — boolean, preserved from v1.x.
- `assert_order_allowed()` — raises `AccountRestrictionError`. **Use this on the
  submission path.** A boolean return can be dropped by a caller; an exception
  cannot.

### Regulatory basis

Registered plans cannot borrow: ITA 146(4)(a) makes an RRSP trust liable for tax
if it has borrowed money, and ITA 146.2(2)(f) requires a TFSA trust to be
prohibited from borrowing. A short sale requires borrowing the security, and
Questrade offers short selling through a margin account. Jurisdiction is Canada
(federal), and these are mandatory tax-law consequences on the plan rather than
advisory guidance. Full citations in `references/standards.md`.

## 6. Treat a 200 as ambiguous on any trade call

Questrade documents an order error returned under `HTTP/1.1 200 OK` carrying
`code: 3054`, `"Order was rejected by the exchange"` and
`orderId: 134353223` — an order that **was** created. `_raise_for_embedded_error`
therefore raises whenever a body carries both `code` and `message`, even on a
200, exposing Questrade's numeric code as `QuestradeAPIError.error_code`.

For anything that mutates state, "the response did not look successful" is not
evidence that nothing happened. Reconcile by `orderId` against
`GET accounts/:id/orders` before any retry — see `order-placement-idempotency`.

## 7. Prefer streaming to polling

20 req/sec across an entire universe is not a quote-polling budget. Questrade
offers WebSocket/RawSocket L1 streaming and order/execution notifications, which
do not consume the REST budget. Constraints worth designing around:

- Only one socket transport at a time; opening a second disconnects the first.
- Ports are stable per URL within a day but differ across URLs — re-request the
  port rather than caching it across days.
- Send the access token as the first socket message **without** a `Bearer`
  prefix.
- "Keeping a socket open does not extend your session" — issue a REST request at
  least every 30 minutes or the socket drops when the session expires. Budget
  that keepalive against the Account-calls quota.
- L1 streaming through the API freezes market data in any other IQ platform in
  use simultaneously.

## Production implementation reference

- Reference code: `scripts/questrade_client.py` — `QuestradeClient`,
  `QuestradeRateLimiter`, `MultiWindowBudget`, `SlidingWindowCounter`,
  `TokenBucketRateLimiter`, `AccountType`, `AccountStatus`, `OrderEligibility`,
  `normalize_api_server`, `categorize_endpoint`.
- Automated unit tests: `scripts/test_questrade_client.py`. Rate-limit tests
  drive an injected fake clock, so an hour of 30 req/sec traffic is verified in
  milliseconds without sleeping.
