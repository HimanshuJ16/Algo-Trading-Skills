# Workflows for Robinhood Unofficial API Integration

## 0. Authorisation gate (do this before anything technical)

1. Establish whether Robinhood has given **express written consent** under
   Customer Agreement §29.1. Without it, §29.1 and §4.7 both bar the integration.
2. Check whether the sanctioned surface covers the use case:
   - Stocks, options or crypto for an eligible US customer → Agentic Trading MCP
     at `https://agent.robinhood.com/mcp/trading`.
   - Crypto only → Crypto Trading API at `https://docs.robinhood.com/`.
3. If either covers it, route there and stop. If not, record the decision, the
   accepted contractual risk, and who accepted it, before continuing.

## 1. Device token provisioning

1. Once per deployment, call `new_device_token()` and write the value to durable
   storage (secret store or a file with restricted permissions).
2. On every start-up, **load** it. Never generate at start-up: a new token per
   process presents a new device to Robinhood and re-triggers approval
   challenges.
3. `RobinhoodUnofficialClient` refuses to construct without one. A missing stored
   token is a provisioning failure — resolve it, do not paper over it.
4. Rotate the token only deliberately, and expect a device-approval challenge on
   the next login when you do.

## 2. Authentication

1. `POST /oauth2/token/` with `client_id`, `grant_type=password`, `username`,
   `password`, `device_token`, `scope=internal`, plus `mfa_code` when one was
   supplied.
2. **Classify the response in this order:**
   - `verification_workflow` present → `RobinhoodDeviceApprovalRequired`. Check
     this *first*: it can arrive on a 200 alongside an `access_token`, and
     reading that as success installs a session that does not exist. No
     `mfa_code` satisfies it; a human must approve the prompt in the Robinhood
     mobile app, and Robinhood delivers approvals only to a trusted device.
   - `mfa_required` → `RobinhoodMFARequired`, carrying `mfa_type`. Re-call
     `authenticate()` with the code.
   - Non-200, or 200 with no `access_token` → `RobinhoodAuthError`. The message
     deliberately does not echo the response body, which can replay submitted
     identifiers into logs.
3. **Validate `expires_in` before installing a session.** Missing, non-numeric,
   zero or negative is fatal. Never substitute a default lifetime.
4. Store the deadline as `time.monotonic() + expires_in`. Wall-clock expiry lets
   an NTP step or DST transition resurrect a dead token or kill a live one.
5. Keep the token out of logs: `AuthToken` excludes the access and refresh tokens
   from `repr`.

A caller that wants to complete a device approval programmatically must implement
the out-of-band flow itself — respond at `/challenge/{id}/respond/`, poll
`/push/{id}/get_prompts_status/` for `challenge_status == "validated"`, then
confirm `workflow_status_approved`. This skill's client deliberately does not,
because there is no unattended path through a prompt that only reaches a trusted
mobile device.

## 3. Order placement

1. Resolve the `account` URL from the account profile and the `instrument` URL
   for the symbol. Neither is derivable from a ticker, and neither may be
   guessed.
2. Validate locally before dispatch — quantity must be finite and positive
   (`0`, negatives, `NaN`, `inf` and non-numerics are refused), a LIMIT order
   must carry a positive finite price, and an extended-hours order must be a
   LIMIT order.
3. Choose the `ref_id`:
   - New logical order → new UUID.
   - Resubmission of the *same* logical order → the **same** `ref_id` as before.
4. Submit `POST /orders/`.
5. **Classify the outcome:**
   - Transport raised (timeout, reset, unparsable) → `RobinhoodAmbiguousOrderError`.
     The request left the process; Robinhood may have accepted it. **Do not
     retry.** Look the order up by `ref_id` in order history first.
   - 2xx with an `id` → order created. A state outside the documented set logs a
     warning; treat the order as live and reconcile.
   - 2xx with no `id` → also ambiguous; there is nothing to reconcile against.
   - Non-2xx → `RobinhoodOrderError`. Nothing was created; this one is safe to
     re-submit after fixing the cause.
6. Remember that a "market" buy is submitted as a collared limit order, so it is
   not guaranteed to fill in a fast market.

## 4. Position polling

1. Start at `GET /positions/?nonzero=true`.
2. Walk `next` to completion. Page one alone silently understates the portfolio —
   which in a reconciliation or exposure check is indistinguishable from a
   correct answer.
3. Guard the walk: a repeated cursor raises, and exceeding `max_pages` raises
   rather than returning a truncated portfolio.
4. Per result:
   - Parse `quantity`, `average_buy_price` and `shares_held_for_sells`;
     unparsable numerics skip the row with a warning rather than crashing the
     poll.
   - Skip `quantity == 0`. Do **not** use `> 0` — a negative quantity is a real
     exposure.
   - The response carries **no ticker symbol**, only an `instrument` URL. Leave
     `symbol` as `None` unless a caller-supplied `symbol_resolver` fills it. A
     placeholder ticker corrupts every downstream report.
5. Space polls by `min_poll_interval_s` (every page included). Robinhood
   publishes no rate limit for these endpoints, so this is a local conservative
   default rather than compliance with a stated budget.
