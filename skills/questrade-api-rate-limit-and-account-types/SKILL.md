---
name: questrade-api-rate-limit-and-account-types
description: >-
  Use when integrating the Questrade IQ API for Canadian equities, ETFs and
  options: single-use OAuth2 refresh-token rotation against a 7-day token
  validity window, the dynamic per-session api_server URL, category-specific
  rate limits (Account calls 30/sec + 30,000/hour; Market Data calls 20/sec +
  15,000/hour), and fail-closed eligibility gating across Questrade's sixteen
  account types where only a Margin account may short.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- questrade
- canadian-markets
- oauth2
- rate-limiting
- registered-accounts
brokers_frameworks:
- Questrade IQ API
- Questrade Practice (paper) environment
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this when a bot reads Questrade account or market data, or gates orders by
Canadian account type. Questrade punishes three specific mistakes:

- **Rotation.** Redeeming a refresh token invalidates it and returns a
  replacement. Lose the replacement and API access is gone until a human
  generates a new token in the API Centre — there is no programmatic recovery.
  The manual-authorization token is itself only valid **7 days** from
  generation, so a bot that sits idle longer than that wakes up locked out.
- **Rate limits.** Two categories, each metered across a per-second *and* a
  per-hour window. A single global 30/sec limiter is wrong in both directions:
  it runs market-data polling 50% over its 20/sec cap while still blowing the
  30,000/hour account cap in under 17 minutes of sustained use.
- **Account types.** `GET v1/accounts` returns one of **sixteen** documented
  types. Only `Margin` can borrow, and a short sale is a borrow. Coercing an
  unrecognised type to `Margin` is how a bot approves a short in a LIRA.

## When NOT to Use

- **To place orders from a personal API app.** Questrade scopes
  `POST accounts/:id/orders`, `POST .../impact` and `DELETE .../orders/:orderId`
  under the `trade` scope, documented as **"partner developers only"**. A
  personal application receives `read_acc` and `read_md`; no order-placement
  endpoint appears anywhere in the public REST reference. This skill provides a
  *pre-submission eligibility gate*, not order submission. Confirm your access
  tier before designing an execution path around this API.
- **As a substitute for pre-trade risk controls.** The eligibility check is a
  broker-capability gate, not an exposure, drawdown or capital control — see
  `kill-switch-and-drawdown-circuit-breakers` and
  `sec-rule-15c3-5-risk-controls-us` for the equivalent control layer.
- **As tax advice on registered accounts.** The borrow prohibition this skill
  enforces is grounded in the Income Tax Act, but eligibility for specific
  option strategies inside a registered plan is a Questrade account-approval
  matter the API does not expose. See `wash-sale-rule-tracking-us` and
  `capital-gains-vs-business-income-classification` for adjacent tax topics,
  and confirm strategy permissions with the broker.
- **Across multiple processes sharing one API key.** Limits are metered per
  key, not per process. Two bots on the same credentials each believe they hold
  the full quota. This limiter is in-process only — see
  `multi-broker-rate-limit-handling` for the distributed case.
- **For high-frequency quote polling.** 20 req/sec on market data is not an HFT
  budget. Use Questrade's L1 streaming socket instead of burning REST quota,
  and note that only one socket connection may be open at a time.

## Prerequisites

- Questrade account with API access activated (API Centre → Activate API) and a
  registered personal app.
- A manual-authorization refresh token, **and durable storage for its
  successor** — a file or secret store written before the token is used, not
  process memory. See `secrets-rotation-without-bot-downtime`.
- Awareness of which login host applies: `login.questrade.com` for live,
  `practicelogin.questrade.com` for practice accounts.
- The account numbers and types you intend to trade, and — if orders are in
  scope — written confirmation of your access tier (personal vs partner).

## Workflow

1. **Redeem the refresh token against the correct host, and persist the
   successor before using it.**
   - `GET https://login.questrade.com/oauth2/token?grant_type=refresh_token&refresh_token={TOKEN}`
     (Questrade also documents this as a `POST`; both appear in its own docs).
   - **Decision point — percent-encode the token.** Questrade's own sample
     tokens contain `+` and `/`. Interpolated raw into a query string, `+`
     decodes server-side as a space and the exchange fails with a token that
     looks perfectly correct in your logs.
   - **Decision point — persistence is part of the exchange, not a follow-up.**
     The submitted token is dead the moment Questrade answers. Write the new
     `refresh_token` to durable storage *before* returning from the exchange,
     and treat a persistence failure as fatal. A process that crashes holding
     only the spent token has lost API access.
   - **Never default `expires_in`.** Questrade's samples show both **300 s** and
     **1800 s**. Assuming 1800 when the server said 300 leaves the client
     confident about a token that died 25 minutes ago. Missing field → fail.

2. **Normalise `api_server` before building any URL.**
   - **Decision point — this field arrives in three shapes.** Questrade's docs
     return `https://api01.iq.questrade.com`,
     `https://api01.iq.questrade.com/` *and* `https://api01.iq.questrade.com/v1`
     in different places. `f"{api_server}v1/accounts"` therefore produces
     `...questrade.comv1/accounts` or `.../v1v1/accounts` for two of the three.
     Strip trailing slashes and any trailing `/v1`, then append exactly one `/`.
   - Reject a non-`https` value: Questrade refuses plaintext, so a `http://`
     value signals a tampered or misparsed response.

3. **Rate-limit per category, across both windows, before dispatch.**
   - Account calls (`time`, `accounts`, `accounts/:id/{positions,balances,executions,orders}`):
     **30/sec and 30,000/hour**. Market Data calls (`markets`,
     `markets/quotes/:id`, `markets/candles/:id`, `symbols/:id`,
     `symbols/:id/options`): **20/sec and 15,000/hour**.
   - **Decision point — a window count is not a refill rate.** A token bucket
     of capacity 30,000 refilling at 30,000/hour starts full and refills, so it
     grants roughly 60,000 requests in the first hour — twice the cap, and a
     ban. Enforce the caps with a sliding-window counter; use a token bucket to
     *pace* traffic below a cap, not to define it.
   - **Decision point — consume windows all-or-nothing.** If the hourly window
     refuses after the per-second window has already been charged, every
     refused attempt leaks capacity from the fast window and the effective
     per-second rate silently collapses.
   - **Decision point — scope membership is not rate-limit membership.**
     `GET markets`, `GET symbols/:id` and `GET symbols/:id/options` sit under
     the `read_acc` *scope* but are metered as *Market Data* calls. Categorise
     from the rate-limit table, not the scope table.
   - Endpoints Questrade categorises in neither table
     (`accounts/:id/activities`, `symbols/search`, `markets/quotes/options`,
     `markets/quotes/strategies`) should inherit the **tighter** budget. That is
     an inference, not a documented figure — over-throttling costs latency,
     under-throttling costs a ban.

4. **Feed the response headers back into the limiter.**
   - Questrade returns `X-RateLimit-Remaining` and `X-RateLimit-Reset` (Unix
     timestamp) on every limited call, and the same headers on a 429.
   - **Decision point — resync downward only.** A *higher* server figure than
     your local estimate usually means the server is counting a different
     process's traffic; trusting it lets both processes claim the full quota.
   - **Decision point — apply the header to the per-second window only.**
     Questrade sends one `X-RateLimit-Remaining` and never says which window it
     describes. Applying `remaining: 29` to the 30,000/hour window charges
     29,971 requests and strands the bot for an hour on a single ambiguous
     header. Ignore a value larger than that window's capacity. (Inferred —
     the header is documented, its scope is not.)
   - **Decision point — classify a 429 on the numeric status, never on message
     text.** `"429" in str(exc)` also matches order id `429123` and limit price
     `429.50`. See `multi-broker-rate-limit-handling` for `Retry-After`-aware
     full-jitter backoff once a throttle is correctly identified.

5. **Map every account, and fail closed on anything unrecognised.**
   - Documented types: `Cash`, `Margin`, `TFSA`, `RRSP`, `FHSA`, `SRRSP`,
     `LRRSP`, `LIRA`, `LIF`, `RIF`, `SRIF`, `LRIF`, `RRIF`, `PRIF`, `RESP`,
     `FRESP`.
   - **Decision point — `Individual` is not an account type.** It is a
     `clientAccountType` (alongside `Joint`, `Corporation`, `Formal Trust`…).
     The two fields are separate and mean different things.
   - **Decision point — never default an unknown type to `Margin`.** The
     enumeration has not been revised since 2015 while Questrade keeps adding
     account types. An unrecognised value must be treated as *maximally
     restricted*, not as the one type with full borrowing privileges.
   - Capture `status` too. Documented values include `Suspended (View Only)`,
     `Liquidate Only` and `Closed`; routing an opening order to any of those is
     a guaranteed rejection at best.

6. **Gate order side against account type and status before submission.**
   - Documented Order Side values are `Buy`, `Sell`, `Short`, `Cov`, `BTO`,
     `STC`, `STO`, `BTC`. **`SellShort` is not a Questrade value.**
   - `Short` and `Cov` require borrowed stock, so they are permitted **only in a
     `Margin` account**. Every registered plan is barred from borrowing — ITA
     146(4)(a) makes an RRSP trust taxable if it borrows, ITA 146.2(2)(f)
     forbids a TFSA trust from borrowing — and a `Cash` account has no margin
     facility.
   - **Decision point — escalate what you cannot verify.** Whether a *covered*
     write (`STO`) is permitted in a given registered plan depends on Questrade
     account approvals the API does not report. Return "review required", not
     "allowed" and not "denied", and make the caller resolve it.
   - **Decision point — a boolean gate can be ignored; an exception cannot.**
     On the submission path, raise rather than return `False`.

7. **Treat a 200 as ambiguous on any trade call.**
   - **Decision point — Questrade documents order errors returned under
     `HTTP/1.1 200 OK`** with a non-zero `code`, a `message` and an `orderId`
     for an order that **was** created. A retry driven by "the call did not
     look successful" can therefore duplicate a live order. Reconcile by
     `orderId` — see `order-placement-idempotency`.

> Full step-by-step procedure: see `references/workflows.md`.
> Verified limits, enumerations and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Interpolating the refresh token into a URL without percent-encoding.**
  Questrade's sample tokens contain `+` and `/`; a raw `+` becomes a space on
  the server and the exchange fails for a reason nothing in your logs explains.
- **Persisting the rotated refresh token after the exchange returns rather than
  during it.** A crash in that gap is unrecoverable without human action: the
  old token is spent and the new one was never written.
- **Forgetting the 7-day validity window on the manual-authorization token.** A
  bot idle across a long holiday weekend, or one whose rotation loop is
  disabled in a maintenance freeze, comes back locked out.
- **Defaulting `expires_in` to 1800.** Questrade documents 300 s too. The client
  then believes a dead token is live for 25 minutes and every call 401s.
- **Hardcoding the API host, or concatenating `api_server` naively.** The field
  is per-session *and* arrives with, without, and with a `/v1` suffix across
  Questrade's own documentation.
- **Pointing a practice refresh token at `login.questrade.com`.** Practice
  accounts redeem against `practicelogin.questrade.com`; the hosts are not
  interchangeable.
- **Modelling one global 30/sec limit.** Market data is 20/sec, and both
  categories carry hourly caps that a per-second limiter never sees.
- **Sizing a refilling token bucket to an hourly cap.** Capacity 30,000 at
  30,000/hour grants ~60,000 in the first hour. Window counts need window
  counters.
- **Categorising endpoints by OAuth scope.** `GET markets` and `GET symbols/:id`
  are `read_acc` scope but Market Data rate limits.
- **Detecting a rate limit by substring.** `"429"` matches order ids and prices;
  a false positive retries a call the broker may already have executed.
- **Ignoring `X-RateLimit-Remaining`, or trusting it upward.** The first wastes
  the broker's own feedback; the second lets two processes on one key each claim
  the full quota.
- **Applying `X-RateLimit-Remaining` to every window.** Questrade sends one
  number for two windows. Charging `remaining: 29` against the 30,000/hour
  budget throttles the bot for an hour on the strength of one header.
- **Coercing an unrecognised account type to `Margin`.** This is the failure
  that approves a short sale in a LIRA, RESP or RRIF. Unknown must mean
  restricted.
- **Treating `Individual` as an account type.** It is a `clientAccountType`;
  conflating the fields loses the actual plan type.
- **Restricting only TFSA/RRSP/FHSA.** SRRSP, LRRSP, LIRA, LIF, RIF, SRIF,
  LRIF, RRIF, PRIF, RESP and FRESP are registered plans too, and a `Cash`
  account cannot short either.
- **Using `"SellShort"` as the order side.** Not a Questrade value; the
  documented short side is `"Short"`.
- **Ignoring account `status`.** `Liquidate Only` and `Suspended (View Only)`
  accounts appear in the list like any other.
- **Reading a 200 on a trade call as "no order exists".** Questrade documents
  the opposite case explicitly, with the `orderId` in the error body.
- **Polling quotes because streaming looked harder.** 20 req/sec across your
  whole universe is the budget; L1 streaming exists precisely to avoid spending
  it, and opening a second socket disconnects the first.

## Verification

- **URL joining:** all three documented `api_server` shapes
  (`…questrade.com`, `…questrade.com/`, `…questrade.com/v1`) must produce
  exactly `https://api01.iq.questrade.com/v1/accounts`. The naive f-string
  produced `…comv1/accounts` and `…/v1v1/accounts` for two of them.
- **Token encoding:** a refresh token containing `+` and `/` must appear in the
  request URL as `%2B` and `%2F`.
- **Rotation:** a persistence callback must be invoked exactly once, before the
  exchange returns, and a raising callback must surface as a fatal auth error
  leaving no session installed.
- **Expiry:** decisions must follow a monotonic deadline, so a wall-clock step
  cannot resurrect or kill a session. A missing or non-numeric `expires_in`
  must raise, never default.
- **Per-second capacities:** a fresh limiter grants exactly 30 Account calls and
  exactly 20 Market Data calls, and the two budgets are independent.
- **Hourly window:** one hour of 30 req/sec bursts must yield ~30,000 grants,
  not ~60,000. (A refilling bucket yields ~60,000 — that is the regression.)
- **All-or-nothing:** with windows 100/sec + 5/min, 5 grants followed by 20
  refusals must leave the per-second window charged exactly 5 times, not 25.
- **Header resync:** `X-RateLimit-Remaining: 3` must reduce the 30/sec budget to
  3 grants and leave the 30,000/hour window **untouched** (applying it to both
  charged 29,971 hourly requests — that is the regression); a subsequent higher
  figure must not restore headroom; a value above the short window's capacity,
  or a malformed one, must be ignored rather than corrupt the budget.
- **429 classification:** an HTTP 429 must raise with `source="server"` and the
  parsed `X-RateLimit-Reset`; an HTTP 400 whose message contains
  `"Order 429123 rejected"` must **not** be classified as a throttle.
- **Local refusal dispatches nothing:** an exhausted local budget must raise
  with `source="local"` and the transport must record zero additional calls.
- **Account types:** all sixteen documented values round-trip; `RDSP` and
  `Individual` both resolve to `UNKNOWN`, never to `MARGIN`.
- **Eligibility:** `Short` is allowed in `Margin` and denied in all fourteen
  registered types plus `Cash` plus `UNKNOWN`; `STO` outside a margin account
  returns `review_required` and folds to `False` through the boolean API;
  `"SellShort"` raises.
- **Status gating:** `Closed`, `Suspended (Closed)`, `Suspended (View Only)` and
  unknown statuses deny every side; `Liquidate Only` permits `Sell` and refuses
  `Buy`.
- **Concurrency:** 64 threads racing a capacity-8 window are granted exactly 8.
- **Secret hygiene:** a failed exchange must not echo the submitted token, and
  `repr()` of a session must not contain the access or refresh token.
- Run `python -m unittest discover -s skills/questrade-api-rate-limit-and-account-types/scripts`
  and confirm all tests pass.

## Related Skills

- `multi-broker-rate-limit-handling`
- `order-placement-idempotency`
- `token-lifecycle-live-probing`
- `secrets-rotation-without-bot-downtime`
- `sandbox-vs-production-endpoint-drift`
- `broker-agnostic-adapter-interface`
- `upstox-oauth-refresh-token-rotation`
