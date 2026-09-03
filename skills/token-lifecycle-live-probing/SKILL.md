---
name: token-lifecycle-live-probing
description: >-
  Use when deciding whether a cached broker token is still usable before trading calls,
  because documented expiry times lie. The probe has three outcomes, and treating the
  inconclusive one as invalid causes needless re-authentication.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, token-expiry, live-probing, session-validity, auth-refresh, cached-token
  brokers_frameworks: "Fyers API v3; ICICI Breeze API; Zerodha Kite Connect"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this whenever bot startup logic needs to decide "reuse cached token" vs
"re-authenticate." Do not use a documented expiry timestamp as the sole source of
truth for whether a token works.

**The timestamp is untrustworthy for reasons you can verify, not just folklore.**
Kite Connect's access token expires at 6 AM the next day, but its documentation
also states the token dies early if it is "invalidated using the API, or invalidated
by a master-logout from the Kite Web trading terminal" — neither of which your bot
observes. Fyers publishes a 15-day refresh-token lifetime but **no time-of-day at
all** for the access token, so any TTL constant you write for Fyers is a guess.
ICICI Breeze expires at 24 hours or midnight, whichever comes first, so the same
token has two different deadlines depending on when it was issued. A cached
timestamp cannot model any of that; a probe does not have to.

**Then get the third outcome right, because that is where this goes wrong in
production.** A probe that does not come back is not a probe that says "dead". A
timeout, a 5xx, or a 429 tells you nothing about the token, and re-authenticating on
it aims a login attempt at a broker that is already degraded — against the endpoint
most likely to throttle you next. The recovery from a five-minute outage becomes a
lockout that outlasts it.

## When NOT to Use

- **As a substitute for the auth flow itself.** This decides whether to re-authenticate,
  not how. See `headless-broker-auth-patterns`.
- **On the critical path of an order.** The probe belongs at startup and after idle
  periods, not in front of every order submission — it adds a broker round trip to a
  latency budget that has none to spare, and a probe that passes microseconds before
  a master-logout still proves nothing about the order.
- **As a way to keep a session alive across days.** For Indian brokers a daily
  logout is mandated (NSE/INVG/67858 Annexure A.8), not incidental. Probing tells
  you the token died; it cannot stop it dying.
- **As a risk control.** Nothing here bounds exposure, order rate, or drawdown. See
  `kill-switch-and-drawdown-circuit-breakers`.
- **With a broker whose only cheap read endpoint has side effects.** If no
  side-effect-free call exists, do not improvise one — see the Breeze note in
  Common Pitfalls.

## Prerequisites

- A cached token store (file, Redis, or DB) with token value + issue timestamp
- A designated cheap, side-effect-free API call to use as a probe (e.g. fetch
  funds/margin, fetch profile) — must be a read-only call, never an order-related
  endpoint. Confirm the **HTTP method** too, not just the path: on Breeze, `GET
  /funds` reads and `POST /funds` sets funds allocation, on the same path.
- The broker's documented auth-failure status codes, kept separate from its
  documented retryable codes — see `references/standards.md`. The defaults
  (`401`/`403` invalid, `408`/`425`/`429` retryable) are not universal.
- For brokers that report errors inside a 2xx envelope (Breeze, and Fyers' `"s":
  "error"` bodies), a body classifier — a status-code check alone will read those
  as healthy.
- A `probe_fn` that catches its own transport errors and returns `(None, True)`,
  rather than letting `requests` raise through the retry loop.
- Exactly one process designated as the token owner for each broker app.

## Workflow

1. On bot startup (or before any trading-critical call after idle periods), retrieve
   the cached token if present.
2. Immediately fire the designated low-cost probe call using the cached token — do
   not gate this behind an expiry-timestamp check first; the check itself is the
   probe, not a timestamp comparison.
3. Classify the probe response into exactly three outcomes, not two:
   - **Valid** (2xx *and* an envelope that does not itself report an error) →
     proceed using the cached token.
   - **Explicitly invalid** (a status the broker documents as an auth failure —
     Kite `403 TokenException`, Fyers `401` with `"code": -16`) → trigger full
     re-authentication immediately.
   - **Ambiguous** (timeout, 5xx, 429, or any status you have not classified) → do
     NOT treat as invalid.
   - **Decision point — which bucket does an unrecognised status fall into?**
     Ambiguous, always. Kite documents `400` as bad parameters, `404` as a missing
     resource, `405` as a wrong method and `410` as gone; every one of those is a
     client or config defect that re-authentication cannot fix, and guessing
     "invalid" spends a login on a bug in your own request.
4. On ambiguity, retry the probe with capped, jittered backoff up to a small bounded
   number of attempts.
   - **Decision point — what happens when the retries are exhausted?** You escalate;
     you do not log in. The token is still not known to be dead. Hold trading, alert,
     and retry on the next cycle. `verify_and_refresh_token` raises
     `AmbiguousProbeError` rather than falling through to `reauth_fn`, and hands the
     cached token back on the exception so the caller can keep it.
   - **Decision point — jitter, not bare exponential backoff.** Every bot in a fleet
     sees the same outage end at the same instant. Undecorrelated retries then
     arrive as one burst against a broker that has just come back.
5. On explicit invalidity, invoke the full auth flow (see
   `headless-broker-auth-patterns`), then re-probe the *new* token before marking the
   bot ready to trade.
   - **Decision point — the new token failed its verification probe. Discard it?**
     No. If the verification probe was ambiguous, the token the broker just issued is
     probably fine and the network is not; throwing it away spends a second login for
     nothing. `TokenVerificationError` carries the new token so it can be persisted
     before the caller retries.
6. Log the token's actual observed lifetime (issue timestamp → invalidation-detected
   timestamp) over time. This builds an empirical picture of real expiry behavior per
   broker — necessary for Fyers, which documents none — and lets you tune when to
   proactively refresh ahead of the observed invalidation point.
   - **Decision point — which statistic drives the proactive refresh?** The
     *minimum* observed lifespan, not the mean: a mean sits comfortably above a
     lifespan you have already watched end sooner. And require several samples first
     — one manual revocation during testing would otherwise drag every future refresh
     forward permanently. `should_proactively_refresh` defaults to 3 samples and a
     30-minute margin.
7. Never assume token validity carries over silently across days without a probe —
   even if the documented expiry says "valid until midnight," probe at bot start
   regardless.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table and sources for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Checking `if now < token_issued_at + documented_ttl: reuse token` and skipping
  the live probe entirely.** This is the exact anti-pattern this skill exists to
  prevent. For Fyers there is no documented TTL to check against in the first place.
- **Collapsing three outcomes into two.** A `probe_fn` that returns `bool` has already
  lost the distinction — false then means both "revoked" and "the broker did not
  answer", and only one of those warrants a login.
- **Classifying `429` as invalidity.** Kite documents `429` as "Too many requests to
  the API (rate limiting)"; Fyers returns `429 request limit reached`. Re-authenticating
  because you were rate-limited is the fastest route to being rate-limited harder.
- **Using an order-placement or order-modification call as the "cheap" probe.** Any
  call with a side effect risks an accidental live action during what should be a
  read-only health check.
- **Probing the right path with the wrong method.** Breeze's `funds` endpoint is a
  read on `GET` and a *funds-allocation write* on `POST`. "It's the funds endpoint,
  it's read-only" is true only of one of the two.
- **Trusting the HTTP status alone on brokers that wrap errors in a 2xx.** Breeze
  returns `{"Success": ..., "Status": <http-style code>, "Error": ...}`, so an expired
  Breeze session can arrive as HTTP 200 and a status-only classifier marks it VALID —
  then the bot trades on a dead session. Fyers similarly carries `"s": "error"` and a
  negative `code` in the body.
- **Treating a single timeout as proof of invalidity and re-authenticating
  aggressively**, which can itself trigger the broker's login-rate-limit and lock the
  bot out longer than the original problem.
- **Retrying in lockstep across a fleet.** Bare exponential backoff without jitter
  turns N bots into one synchronised burst the moment the broker recovers.
- **Discarding a freshly issued token because its verification probe timed out.** The
  login succeeded; the probe did not. Persist it, then re-verify.
- **Not persisting observed invalidation timing**, which means every engineer
  re-learns the broker's real behavior from scratch instead of having monitoring data
  to act on.
- **Clamping a nonsensical lifespan observation to zero instead of rejecting it.** A
  clock-skew or bookkeeping error then enters the empirical baseline as a 0-second
  lifetime, and every subsequent token looks overdue for refresh.
- **Putting token material in a log line or an exception message.** The probe path is
  the one place holding a live credential in a variable; alerts from it get shipped
  to a log aggregator.
- **Letting the alert channel decide the trade.** If the pager or webhook call raises,
  an unguarded `alert_fn` replaces `AmbiguousProbeError` with a `ConnectionError` — and
  the caller's "do not spend a login" branch, bound to `AmbiguousProbeError`, never
  runs. A failing alert must not change the verdict.
- **Passing a raw HTTP call as `probe_fn`.** A bare `requests.get` raises
  `ConnectionError`/`Timeout` instead of returning a status, which skips the retry
  path entirely and turns a transient blip into a hard startup failure. `probe_fn`
  owns the translation to `(None, True)`.
- **Running more than one token owner per broker app.** Two workers probing
  concurrently can both see INVALID and both re-authenticate, spending two logins and
  leaving one holding a superseded token. Nothing here is synchronised, and an
  in-process lock would not help across processes: elect one owner, and have the rest
  read the token it publishes.

## Verification

- Simulate an expired/invalidated token in a test environment (e.g. manually revoke,
  or use an intentionally malformed token) and confirm the bot detects it via the
  probe and re-authenticates without manual intervention.
- Simulate an *ambiguous* outcome — a forced timeout, a stubbed 503, a stubbed 429 —
  and confirm the login flow is **not** invoked, the alert fires, and the cached token
  is retained. This is the property most likely to be silently wrong.
- Confirm the probe call never appears in the broker's order history/audit log
  (proving it has no trading side effects), and that the probe is issued with a
  read-only HTTP method.
- For Breeze or Fyers, confirm an error carried inside a 2xx body is classified
  INVALID, not VALID.
- Check logs after a multi-day run show accurate classification of
  valid/invalid/ambiguous outcomes with no unnecessary re-logins during a known broker
  outage window.
- Confirm no alert, log line, or exception message emitted by the probe path contains
  token material.
- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/token-lifecycle-live-probing/scripts`.

## Related Skills

- `headless-broker-auth-patterns`
- `multi-broker-rate-limit-handling`
- `paper-to-live-promotion-checklist`
- `secrets-rotation-without-bot-downtime`
