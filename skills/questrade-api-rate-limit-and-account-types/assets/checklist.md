# Pre-Flight / Sign-off Checklist — questrade-api-rate-limit-and-account-types

Use this before pointing a bot at a live Questrade account.

## Access tier

- [ ] **Order capability confirmed in writing.** Questrade's `trade` scope
      (`POST accounts/:id/orders`, `DELETE …/orders/:orderId`) is documented as
      "partner developers only". If this is a personal API app, confirm the
      design does not assume order submission through this API.
- [ ] **Scopes granted match the calls made** — `read_acc` and/or `read_md`.

## OAuth2 and token lifecycle

- [ ] **Correct login host.** `login.questrade.com` for live,
      `practicelogin.questrade.com` for practice. Verified against the account
      actually in use, not assumed.
- [ ] **Refresh token percent-encoded** before it enters the query string
      (Questrade tokens contain `+` and `/`; a raw `+` decodes to a space).
- [ ] **Rotated refresh token persisted durably inside the exchange**, before
      the call returns, and a persistence failure is fatal rather than logged.
- [ ] **Storage is not process memory.** A restart must be able to resume from
      the last rotated token.
- [ ] **`expires_in` read from the response, never defaulted.** Questrade
      documents both 300 s and 1800 s.
- [ ] **Expiry decisions use a monotonic clock**, so an NTP step cannot
      resurrect or prematurely kill a session.
- [ ] **7-day cliff alarmed.** An alert fires on *time since last successful
      rotation*, not only on call failures — a bot idle beyond 7 days is locked
      out and recovery requires a human in the API Centre.
- [ ] **Revocation path known** (`POST /oauth2/revoke`, or API Centre → Revoke)
      and tested at least once.
- [ ] **No token in logs.** A failed exchange does not echo the submitted token;
      `repr()` of a session object does not expose credentials.

## Dynamic endpoint

- [ ] **`api_server` taken from the OAuth response every session**, never
      hardcoded.
- [ ] **`api_server` normalised before joining.** Verified against all three
      documented shapes: `…questrade.com`, `…questrade.com/`, `…questrade.com/v1`.
- [ ] **Non-`https` `api_server` rejected.**

## Rate limiting

- [ ] **Both categories modelled separately** — Account calls 30/sec +
      30,000/hour; Market Data calls 20/sec + 15,000/hour.
- [ ] **Both windows per category enforced.** A per-second-only limiter blows
      the hourly cap in under 17 minutes of sustained polling.
- [ ] **Caps enforced with window counters, not refilling buckets.** A bucket
      sized to 30,000/hour grants ~60,000 in the first hour.
- [ ] **Windows consumed all-or-nothing**, so a refused attempt does not leak
      capacity from the faster window.
- [ ] **Endpoints categorised from the rate-limit table, not the scope table.**
      `GET markets` and `GET symbols/:id` are `read_acc` scope but Market Data
      limits.
- [ ] **Uncategorised endpoints inherit the tighter budget**
      (`accounts/:id/activities`, `symbols/search`, `markets/quotes/options`,
      `markets/quotes/strategies`).
- [ ] **`X-RateLimit-Remaining` consumed, and applied downward only.**
- [ ] **The header is scoped to the per-second window**, not applied to the
      hourly one — Questrade sends one number for two windows, and charging
      `remaining: 29` against 30,000/hour throttles the bot for an hour.
- [ ] **429 classified on the numeric status**, never by substring — `"429"`
      also matches order ids and prices.
- [ ] **Every wait has a deadline.** No unbounded spin against an exhausted
      hourly budget.
- [ ] **Local refusal provably dispatches nothing**, so a mutating call refused
      locally cannot have executed broker-side.
- [ ] **Streaming used instead of quote polling** where feasible, and the
      30-minute REST keepalive is budgeted against the Account quota.

## Account types and status

- [ ] **All sixteen documented account types handled**: Cash, Margin, TFSA,
      RRSP, FHSA, SRRSP, LRRSP, LIRA, LIF, RIF, SRIF, LRIF, RRIF, PRIF, RESP,
      FRESP.
- [ ] **Unrecognised type is treated as restricted, never coerced to `Margin`.**
      This is the failure mode that approves a short sale in a LIRA.
- [ ] **`clientAccountType` kept distinct from `type`.** `Individual` is not an
      account type.
- [ ] **Account numbers kept as strings** (eight digits, leading zeros
      preserved).
- [ ] **Registry rebuilt on each fetch**, so closed accounts do not linger.
- [ ] **`status` enforced.** `Suspended (View Only)`, `Suspended (Closed)` and
      `Closed` reject all sides; `Liquidate Only` permits only position-reducing
      sides.

## Order eligibility gating

- [ ] **`Short` and `Cov` permitted only in a `Margin` account** — every
      registered plan is barred from borrowing (ITA 146(4)(a), 146.2(2)(f)) and
      `Cash` has no margin facility.
- [ ] **Documented order sides used**: `Buy`, `Sell`, `Short`, `Cov`, `BTO`,
      `STC`, `STO`, `BTC`. `SellShort` is not a Questrade value.
- [ ] **Option-writing eligibility in registered plans escalated, not assumed**
      in either direction, and confirmed with Questrade before going live.
- [ ] **Submission path raises rather than returning a boolean** that a caller
      could ignore.

## Order-state safety (if trade access applies)

- [ ] **A 200 is not treated as unconditional success.** Questrade documents
      order errors under HTTP 200 carrying a non-zero `code` and an `orderId`
      for an order that *was* created.
- [ ] **Reconciliation by `orderId` before any retry** — see
      `order-placement-idempotency`.

## Testing and operations

- [ ] `python -m unittest discover -s skills/questrade-api-rate-limit-and-account-types/scripts`
      — all tests pass.
- [ ] `python tools/validate_skills.py` — passes.
- [ ] **Practice environment exercised first**, including a full token rotation
      and a deliberate rate-limit refusal.
- [ ] **A second process on the same API key is accounted for** (or prevented) —
      limits are metered per key, not per process.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Access tier (personal / partner): ___________________________
- Environment verified (practice / live): ___________________________
