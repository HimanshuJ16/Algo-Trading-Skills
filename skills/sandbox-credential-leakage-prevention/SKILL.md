---
name: sandbox-credential-leakage-prevention
description: >-
  Use when one process runs in both paper and live modes and a misrouted request would
  burn real capital. An allow-list gate that refuses any outbound call whose parsed
  hostname and path do not match the declared environment.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: sandbox-isolation, credential-leakage, devsecops, secret-guard, broker-endpoints, allow-list, fail-closed
  brokers_frameworks: "Alpaca Trading API v2; Binance Spot & Futures API; Saxo Bank OpenAPI; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading application runs in dual modes — paper/sandbox and live production — and a single misrouted request would either burn real capital or silently reject a paper order. `CredentialEnvironmentGuard.validate_request_boundary()` is a veto gate called immediately before each outbound broker HTTP call: it parses the destination URL and requires the hostname (plus a path prefix, where the venue separates environments by path) to positively match an endpoint declared for the environment the process says it is running in.

The design decision that matters is **allow-list, not deny-list**. A guard built as "reject the URL if it contains a known live hostname" approves every destination its author did not think to enumerate — including look-alike domains, hosts the venue added after the rules were written, and any URL where the word `paper` happens to appear in a query parameter. This engine inverts that: the request is refused unless it is recognised. An unknown broker is a violation, not a pass.

Its second job is **not becoming a leak itself**. Broker URLs routinely carry secrets in the query string (Binance appends `&signature=<hmac>` to signed REST calls). Every URL reaching an exception message or a log line is stripped of userinfo, query, and fragment first.

## When NOT to Use

- **Single-endpoint venues**: If one base URL serves both environments and only the credential differs, the endpoint allow-list has nothing to decide. Use credential-scope controls instead (`api-key-least-privilege-audit-tool`).
- **As the only Alpaca control**: For Alpaca specifically, `alpaca-paper-live-key-separation` adds a live `GET /v2/account` probe of the `is_paper` flag and an `ALLOW_LIVE_TRADING` gate. This guard validates the request boundary; it never contacts the broker and so cannot detect that correct-looking credentials belong to the wrong account.
- **Backtests and replay harnesses**: No network calls to a broker means no boundary to enforce.
- **Secret storage and rotation**: This is a runtime egress check, not a vault. See `centralized-secrets-management-vault-integration` and `secrets-rotation-without-bot-downtime`.
- **As a substitute for separate credential stores**: The guard catches a mixed-up credential at the last moment. It does not excuse loading paper and live secrets into the same process.

## Prerequisites

- An explicitly declared `TradingEnvironment` (`SANDBOX` or `PRODUCTION`) sourced from deployment configuration, not inferred from a URL or a key.
- `BrokerEnvironmentRules` per broker in use: `broker_name`, `sandbox_endpoints`, `production_endpoints`, and optionally `sandbox_key_prefixes` / `production_key_prefixes`. Endpoints are `"host"` or `"host/path/prefix"` shorthand, or `EndpointRule` objects.
- Every outbound broker call routed through the guard. A single HTTP client that bypasses it defeats the control entirely.

## Workflow

1. **Declare the environment once, at process start.**
   - Construct `CredentialEnvironmentGuard(TradingEnvironment.SANDBOX | PRODUCTION)`. Passing anything other than the enum raises `TypeError` rather than being coerced.
   - Leave `allow_unknown_brokers` at its default `False`. Setting it `True` disables the boundary check for unregistered brokers and logs a warning each time — treat it as an explicit, reviewed risk acceptance, never a convenience default.

2. **Validate the destination URL structurally, before comparing it to anything.**
   - Reject non-`https` schemes: an `http://` broker call sends the API key in cleartext headers.
   - Reject userinfo in the URL. `https://api.alpaca.markets@evil.example/v2/orders` reads as the Alpaca host and resolves to `evil.example`; credentials in a URL are the leak this skill exists to stop.
   - Reject a missing hostname and any port other than 443.

3. **Match the destination against the environment's allow-list, on parsed components.**
   - Compare the **exact** lower-cased hostname. Never a substring: `"api.alpaca.markets" in url` also matches `https://api.alpaca.markets.attacker.example/v2/orders`.
   - Where a venue separates environments by path rather than host, require the path prefix too. Saxo Bank is the shipped case — simulation is `gateway.saxobank.com/sim/openapi` and live is `gateway.saxobank.com/openapi` on the *same hostname*, so a host-only check treats live and sim as interchangeable.
   - Normalise the path before comparing, so `/sim/openapi/../../openapi/port/v1/orders` is judged as the live path it actually resolves to.
   - No match against this environment's list is a violation. If the destination matches the *opposing* environment's list, say so explicitly — that is the cross-environment leak, and it is the more actionable message.

4. **Treat the key prefix as corroboration, never as the decision.**
   - A key carrying the *opposing* environment's prefix (`AK…` in sandbox mode) is a hard violation — that is a high-confidence positive signal.
   - A key matching *none* of the expected prefixes only logs a warning. Alpaca's authentication documentation specifies no key format at all, so the observed `PK`/`AK` convention is not a contract; Binance keys and Saxo OAuth tokens have no prefix whatsoever. Absence of a prefix proves nothing, and blocking on it would break every venue without a prefix scheme.
   - Compare prefixes case-insensitively: a lower-cased `ak_live_…` is still a production-shaped key.

5. **Redact before reporting.** Strip userinfo, query, and fragment from every URL that reaches a `SecurityViolationError` message or a log record. Never log the API key; report only which prefix matched.

6. **Review the shipped allow-list on a schedule.** `iter_declared_endpoints()` dumps every `(broker, environment, endpoint)` triple for diffing against current vendor documentation. Venues add and retire hosts without changing their API version — Binance alone serves production spot from six hostnames.

> Full procedure: see `references/workflows.md`.
> Endpoint facts and citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Substring matching the URL instead of the parsed host.** `"paper" in url` approves `https://api.alpaca.markets/v2/orders?client_tag=paper` — a live order, in sandbox mode, blessed by the guard because an unrelated query parameter contained the word. Any allow or deny decision driven by attacker- or operator-controlled URL text is not a control.
- **Checking only "is not the other environment".** Verifying that a production URL is not a sandbox URL leaves `https://attacker.example/v2/orders` approved, and with it the live credential in the request headers. Both directions need a positive match.
- **Failing open on an unregistered broker.** The case where no rules exist is precisely the case with no protection; returning `True` there converts the guard into decoration. Fail closed and make the operator register the broker or opt out in writing.
- **Assuming a registrable domain identifies an environment.** Binance serves production market data from `data-api.binance.vision` and the spot testnet from `testnet.binance.vision` — same domain, opposite environments.
- **Treating an undocumented key prefix as a contract.** `PK`/`AK` is an observed Alpaca convention, not a documented one, and most venues have no prefix at all. Gate on the endpoint; corroborate with the prefix.
- **Echoing the full URL into the exception.** A signed Binance URL carries `&signature=<hmac>`; an OAuth callback carries the token. A leak-prevention guard that writes those into logs and tracebacks has become the leak.
- **Calling the HTTP client directly.** One code path that skips `validate_request_boundary()` — a health check, a retry helper, a vendored SDK — is the path the bad request will take.

## Verification

- `CredentialEnvironmentGuard(SANDBOX)` with `PK…` to `https://paper-api.alpaca.markets/v2/orders` $\implies$ returns `True`.
- Same guard to `https://api.alpaca.markets/v2/orders?client_tag=paper` $\implies$ raises `SecurityViolationError("ENDPOINT LEAK DETECTED…")`. This is the regression case: the previous substring implementation approved it.
- `CredentialEnvironmentGuard(PRODUCTION)` with `AK…` to `https://api.alpaca.markets.attacker.example/v2/orders` $\implies$ raises `SecurityViolationError`, message containing `not a recognised`.
- `CredentialEnvironmentGuard(PRODUCTION)` with a `PK…` key $\implies$ raises `SecurityViolationError("CREDENTIAL LEAK DETECTED…")`.
- Any unregistered broker $\implies$ raises `SecurityViolationError` unless `allow_unknown_brokers=True` was passed explicitly.
- A URL carrying `&signature=<secret>` $\implies$ the secret does not appear in the raised message.
- Run `python -m unittest discover -s skills/sandbox-credential-leakage-prevention/scripts` (46 tests).

## Related Skills

- `alpaca-paper-live-key-separation`
- `binance-futures-testnet-to-mainnet-promotion`
- `sandbox-vs-production-endpoint-drift`
- `centralized-secrets-management-vault-integration`
- `paper-to-live-promotion-checklist`
