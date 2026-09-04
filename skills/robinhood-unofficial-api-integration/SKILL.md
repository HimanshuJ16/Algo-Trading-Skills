---
name: robinhood-unofficial-api-integration
description: >-
  Use when assessing programmatic access to a Robinhood account. Its Customer Agreement
  requires express written consent before any API reaches the account, and sanctioned
  alternatives now exist, so the contractual finding usually settles it.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, robinhood, unofficial-api, us-equities, order-idempotency, device-approval
  brokers_frameworks: "Robinhood unofficial REST endpoints (api.robinhood.com); Robinhood Agentic Trading MCP server (official); Robinhood Crypto Trading API (official); robin_stocks (community reference)"
  version: "2.0.1"
  author: algo-trading-skills-contributors
---

## When to Use

Use this when evaluating — or, having obtained Robinhood's consent, building —
programmatic access to a Robinhood brokerage account for US equities.

**Start with the contractual finding, because it is usually the whole answer.**
The RHF-RHS Customer Agreement (effective 2026-07-02) states in §29.1 that a
customer **"may not use the API Package or develop Licensee Products without
Robinhood's express written consent (and Robinhood may decline any such request
for use or development in its sole discretion)"**, and in §4.7 that **"You agree
not to allow any person access to your Account, your Account username or
password, or permit any other person to give orders or instructions on your
Account to Robinhood, without the prior consent of Robinhood."** Credential-based
automation of `api.robinhood.com` is squarely inside both clauses.

**And there is now a supported path that did not exist when tooling like this was
written.** Robinhood opened its Agentic Trading MCP server —
`https://agent.robinhood.com/mcp/trading` — to third-party agents for stocks and
options on 2026-05-27 and for crypto on 2026-07-20, free to eligible US
customers, and has published a Crypto Trading API at `https://docs.robinhood.com/`
since 2024. If your use case is covered there, this skill's answer is to route
through the supported surface, not these endpoints.

If you proceed anyway, this skill covers what the unofficial integration must get
right: device-token persistence, a login flow that an `mfa_code` no longer
satisfies, `ref_id` as the only reconciliation handle on an order, and a
`/positions/` response that is paginated and carries no ticker symbol.

## When NOT to Use

- **As authorisation.** Nothing here grants consent, and no code quality
  substitutes for it. §29.1 requires *express written* consent; the realistic
  downside of proceeding without it is account restriction or termination.
- **Where the sanctioned surface covers you.** Agentic Trading trades stocks,
  options and crypto through a dedicated agentic account with user-held controls,
  and the Crypto Trading API covers crypto with Ed25519-signed API keys. Prefer
  either, and see `broker-agnostic-adapter-interface` to keep the strategy
  portable.
- **For unattended/headless operation.** Robinhood has moved login MFA to in-app
  device approvals and states approvals "only go to a trusted Robinhood device".
  A headless server is never a trusted device, so there is no unattended login
  path here. See `headless-broker-auth-patterns` before designing around this.
- **For client money or third-party accounts.** Operating someone else's account
  through unconsented automation compounds a contractual breach with a likely
  regulatory problem.
- **As a market-data source.** These endpoints are not a licensed feed; see
  `market-data-entitlement-and-licensing-per-venue`.
- **As a risk control.** The validation here is input hygiene, not an exposure,
  drawdown or capital control. See `kill-switch-and-drawdown-circuit-breakers`
  and `sec-rule-15c3-5-risk-controls-us`.

## Prerequisites

- Documented confirmation of your authorisation under Customer Agreement §29.1,
  or an explicit, recorded acceptance of the contractual risk.
- Robinhood credentials, and a human able to approve a device prompt in the
  Robinhood mobile app when Robinhood raises one.
- A **persisted** device token — generated once by `new_device_token()`, written
  to durable storage, and reused on every login thereafter.
- The account URL from Robinhood's account profile, and an instrument URL per
  symbol. Neither is derivable from a ticker.
- A caller-supplied `http_fn` transport, so timeouts and TLS verification stay
  under caller control.
- An OAuth `client_id` you are authorised to use. The reference client ships no
  default: the id community libraries pass is harvested from Robinhood's own web
  client, and redistributing it is part of the contractual problem this skill exists
  to surface.

## Workflow

1. **Resolve the contractual question before writing any code.**
   - **Decision point:** if consent under §29.1 has not been obtained and the use
     case is served by Agentic Trading or the Crypto Trading API, stop and route
     there. The remaining steps assume that decision was made deliberately and
     recorded.

2. **Load the persisted device token; never mint one at start-up.**
   - The token is a *device identity*. A fresh UUID per process makes every
     restart look like a new device, which is what drives repeated approval
     challenges and security flags — the failure this skill's own pitfalls warn
     about, and the one the pre-2.0 reference client committed in its
     constructor.
   - **Decision point:** a missing stored token is a provisioning failure, not a
     reason to generate one silently. `RobinhoodUnofficialClient` refuses to
     construct without one.

3. **Authenticate, and classify the challenge before deciding what to do.**
   - `POST /oauth2/token/` with `client_id`, `grant_type=password`, `username`,
     `password`, `device_token`, `scope=internal`.
   - **Decision point — `verification_workflow` is not an MFA code prompt.** It
     is Robinhood's device-approval flow, completed by tapping a prompt in the
     mobile app; no `mfa_code` satisfies it. Check for it *before* reading
     `access_token`: it can arrive on a 200, and treating that as success
     installs a session that does not exist. It raises
     `RobinhoodDeviceApprovalRequired`, carrying the workflow id for a caller
     that implements the out-of-band poll itself.
   - `mfa_required` raises `RobinhoodMFARequired` with the reported `mfa_type`.
     Both subclass `RobinhoodAuthError`, so a caller catching the base class is
     unaffected; classify on the type, never by substring-matching the message.
   - **Decision point — never default `expires_in`.** A client that assumes a
     lifetime the server did not state keeps using a dead token, and every
     subsequent call 401s for a reason nothing in the logs explains. A missing or
     non-numeric value is fatal. Expiry is tracked against a monotonic deadline
     so a clock step cannot resurrect or kill a session.

4. **Submit orders with a client-generated `ref_id`, and treat a lost response as
   ambiguous.**
   - The payload needs the real `account` URL, the `instrument` URL, `symbol`,
     `side`, `type`, `quantity`, `time_in_force`, `trigger` and `ref_id`.
   - **Decision point — `ref_id` is the only handle you control.** Reuse the same
     value when resubmitting a logically identical order; generate a new one for
     a genuinely new order. Robinhood publishes no idempotency contract for it,
     so treat it as a reconciliation key first and a duplicate guard second.
   - **Decision point — a timeout is not a failure.** If the transport raises,
     the request already left the process and Robinhood may have accepted it.
     `RobinhoodAmbiguousOrderError` carries the `ref_id`; reconcile against order
     history before any resubmission. See `order-placement-idempotency`.
   - **Decision point — a 2xx with no order id is also ambiguous**, not a
     success: there is nothing to reconcile against.
   - **A "market" buy is not a market order.** Robinhood's own clients submit it
     as a limit order collared a few percent above the ask, so it can fail to
     fill in a fast market whatever `type` you send. Size and monitor
     accordingly.
   - Validate before dispatch: zero, negative, NaN, infinite and non-numeric
     quantities; a LIMIT order with no price; extended-hours on a non-limit
     order. An invalid order must never reach the broker.

5. **Poll `/positions/` across every page, and never fabricate a symbol.**
   - **Decision point — the response carries no ticker.** It has an `instrument`
     URL only. Defaulting the missing field to a placeholder makes every position
     reconcile as the same fake symbol. `RobinhoodPosition.symbol` stays `None`
     unless a caller-supplied `symbol_resolver` fills it.
   - **Decision point — the response is paginated.** Reading page one only
     silently understates the portfolio, which is a capital-safety defect in any
     reconciliation or exposure check. Follow `next` to completion; exceeding
     `max_pages`, or a looping cursor, raises rather than returning a truncated
     portfolio.
   - Request `?nonzero=true` and filter locally on `quantity != 0` — a negative
     quantity is a real exposure, not noise to drop.
   - Space polls with a local minimum interval. Robinhood publishes **no** rate
     limit for these endpoints, so this is a conservative local default, not
     compliance with a stated budget.

> Full step-by-step procedure: see `references/workflows.md`.
> Verified endpoints, fields and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating "Robinhood has no official API" as still true.** It is the premise
  most Robinhood automation tooling was built on, and it stopped being accurate
  in 2024 (Crypto Trading API) and again in 2026 (Agentic Trading MCP). Building
  an unconsented integration when a sanctioned one exists is the expensive
  mistake here.
- **Minting a device token in the constructor.** Every restart then presents a
  new device and re-triggers approval challenges. Generate once, persist, reuse.
- **Expecting `mfa_code` to complete a login.** Robinhood has moved to in-app
  device approvals, and approvals reach only a *trusted* device — which a
  headless host never is. Community wrappers break on this repeatedly.
- **Reading a 200 that carries `verification_workflow` as a successful login.**
  The session was not issued; the next authenticated call fails without a clear
  cause.
- **Detecting the MFA case by substring-matching an error message.** Classify on
  the exception type.
- **Defaulting `expires_in`.** The client then believes a dead token is live.
- **Using wall-clock time for token expiry.** An NTP step or DST transition
  should not be able to resurrect or kill a session.
- **Submitting an order without a `ref_id`.** After a timeout there is then no
  handle to reconcile with, and the only options are "retry and risk a duplicate"
  or "do nothing and risk an unmanaged position".
- **Retrying an order because the HTTP request timed out.** The broker may have
  already accepted it. Reconcile by `ref_id` first.
- **Hardcoding an account URL** (the pre-2.0 client shipped a literal
  `/accounts/MOCK/`). A guessed account URL addresses the wrong account, and a
  placeholder one guarantees rejection.
- **Sending only a ticker on an order.** `/orders/` keys the security off the
  `instrument` URL; the ticker alone is not enough.
- **Treating a "market" order as guaranteed to fill.** It is submitted as a
  collared limit order.
- **Defaulting the absent `symbol` on a position to a placeholder.** Every real
  position then reports the same fake ticker. An absent symbol is correct; a
  fabricated one corrupts every downstream report.
- **Reading only the first page of `/positions/`.** Silent portfolio truncation,
  and it looks exactly like a correct empty-tail response.
- **Dropping negative-quantity positions with a `> 0` filter.** That is exposure
  disappearing from a risk view.
- **Quoting an unsourced polling rate as a broker limit.** Robinhood publishes
  none; label local defaults as local defaults.
- **Letting `AuthToken` reach a log.** Exclude access and refresh tokens from
  `repr`, and never echo an auth response body into an exception message.

## Verification

- **Device token:** construction without a token, or with a blank one, raises;
  two clients built from the same stored token send an identical `device_token`
  (the pre-2.0 constructor sent a different one every time — that is the
  regression).
- **Transport:** a missing transport fails at construction, not at the first
  order.
- **Device approval:** a response containing `verification_workflow` raises
  `RobinhoodDeviceApprovalRequired` with the workflow id, is not a
  `RobinhoodMFARequired`, and installs no session — including when it arrives
  alongside a 200 with an `access_token`.
- **MFA:** `mfa_required` raises with the reported `mfa_type` and no session;
  the retry with `mfa_code` succeeds. Both exceptions remain `RobinhoodAuthError`
  subclasses.
- **Expiry:** a missing, non-numeric, zero or negative `expires_in` is fatal;
  expiry decisions follow a monotonic deadline; an expired token blocks both
  order placement and position polling.
- **Secret hygiene:** a failed authentication must not echo the submitted email
  back in the message, and `repr()` of a token or client must not contain the
  access or refresh token.
- **Order payload:** every submission carries a UUID `ref_id`, the configured
  account URL (never `MOCK`) and the supplied instrument URL; a caller-supplied
  `ref_id` is sent verbatim on both attempts of a resubmission; distinct orders
  get distinct ids.
- **Order validation:** quantity `0`, `-1`, `NaN`, `inf`, `"10"`, `None` and
  `True`, a LIMIT order with no or non-positive price, and extended-hours on a
  market order each raise before anything is dispatched — the transport must
  record zero order calls.
- **Ambiguity:** a transport exception raises `RobinhoodAmbiguousOrderError`
  carrying the `ref_id` and records no order; a 2xx with no `id` does the same; a
  definitive HTTP 400 rejection raises `RobinhoodOrderError` instead.
- **Positions:** `symbol` is `None`, never a placeholder, and the instrument URL
  is preserved; a resolver fills the ticker; a two-page response returns both
  positions (page-one-only returned one — that is the regression); the first
  request carries `nonzero=true`; a runaway cursor and a looping cursor each
  raise rather than truncate; a negative quantity is retained; an unparsable
  position is skipped with a warning rather than crashing the poll.
- **Throttling:** the second consecutive poll requests a sleep, each extra page
  is throttled too, and a zero interval requests none.
- Run `python -m unittest discover -s skills/robinhood-unofficial-api-integration/scripts`
  and confirm all tests pass.

## Related Skills

- `degiro-unofficial-api-risk-assessment`
- `headless-broker-auth-patterns`
- `order-placement-idempotency`
- `broker-agnostic-adapter-interface`
- `broker-api-deprecation-notice-monitoring`
- `pattern-day-trader-rule-compliance-us`
- `token-lifecycle-live-probing`
