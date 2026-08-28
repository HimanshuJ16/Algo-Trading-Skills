# Pre-Flight Checklist — Robinhood Unofficial API Integration

## Authorisation (answer before writing code)

- [ ] Has Robinhood given **express written consent** under Customer Agreement
      §29.1? If not, has the contractual risk been explicitly recorded and
      accepted by a named owner?
- [ ] Has the sanctioned path been ruled out — Agentic Trading MCP
      (`https://agent.robinhood.com/mcp/trading`) for stocks, options and crypto,
      or the Crypto Trading API for crypto?
- [ ] Is this our own account, not a client's or a third party's?

## Session

- [ ] Is the device token generated **once**, persisted to durable storage, and
      loaded on every start-up — never minted in the constructor?
- [ ] Does a missing stored device token fail loudly instead of generating a new
      one?
- [ ] Is a `verification_workflow` response classified as device approval, and
      checked **before** `access_token`, so a 200 carrying one never installs a
      session?
- [ ] Is there a human able to approve the device prompt in the Robinhood mobile
      app? (Approvals reach only a trusted device — a headless host is not one.)
- [ ] Is a missing or non-numeric `expires_in` fatal, with no default lifetime
      substituted?
- [ ] Is token expiry tracked on a monotonic deadline, not wall-clock time?
- [ ] Are access and refresh tokens excluded from `repr` and from every log line
      and exception message?

## Orders

- [ ] Does every submission carry a client-generated `ref_id`?
- [ ] Is the **same** `ref_id` reused for a resubmission of the same logical
      order, and a new one for a genuinely new order?
- [ ] Is a transport timeout treated as an **ambiguous** outcome — reconcile by
      `ref_id`, never blind-retry?
- [ ] Is a 2xx with no order id also treated as ambiguous?
- [ ] Are the real `account` and `instrument` URLs supplied, with no placeholder
      or guessed account?
- [ ] Are zero, negative, `NaN`, infinite and non-numeric quantities rejected
      before dispatch?
- [ ] Does a LIMIT order require a positive finite price, and an extended-hours
      order require LIMIT type?
- [ ] Is it understood that a "market" buy is submitted as a collared limit order
      and may not fill in a fast market?
- [ ] Are day-trade / DTBP override flags left unset?

## Positions

- [ ] Is `/positions/` pagination followed to completion, with a bound that
      raises rather than returning a truncated portfolio?
- [ ] Is a looping pagination cursor detected?
- [ ] Is `symbol` left `None` rather than filled with a placeholder when no
      resolver is supplied?
- [ ] Is the zero filter `quantity != 0`, so negative exposures are retained?
- [ ] Are unparsable position rows skipped with a warning rather than crashing
      the poll?

## Operational

- [ ] Is polling spaced by a local minimum interval, documented as a local
      default rather than a published Robinhood limit?
- [ ] Is the HTTP transport caller-supplied, with explicit timeouts and TLS
      verification?
- [ ] Is there endpoint-schema drift monitoring, given that these endpoints can
      change without notice?
- [ ] Are risk limits (exposure, drawdown, kill switch) enforced **outside** this
      client, not inside it?
