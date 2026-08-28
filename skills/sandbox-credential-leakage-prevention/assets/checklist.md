# Pre-Flight Checklist — Sandbox Credential Leakage Prevention

## Environment declaration

- [ ] Is `TradingEnvironment` sourced from deployment configuration, and never inferred from the URL or key being validated?
- [ ] Is `allow_unknown_brokers` left at its default `False`? If it was set `True`, is the risk acceptance recorded and reviewed?
- [ ] Are paper and live credentials held in separate stores, so the guard is the last line of defence rather than the only one?

## Rule configuration

- [ ] Does every broker the application calls have a registered `BrokerEnvironmentRules` entry?
- [ ] Do the declared endpoints cover **all** hosts the venue serves, not just the one in the getting-started guide? (Binance production spot alone is six hostnames.)
- [ ] For venues that separate environments by path rather than host (Saxo), is the path prefix declared on both sides?
- [ ] Are key prefixes declared **only** where the venue actually has a prefix scheme, and understood as advisory rather than as the deciding check?
- [ ] When extending the shipped defaults, was `BROKER_RULES` copied in — remembering that `custom_rules` replaces rather than merges?

## Enforcement coverage

- [ ] Is `validate_request_boundary()` called inside the single HTTP wrapper every broker request passes through?
- [ ] Have health checks, retry helpers, webhook callbacks, and vendored SDK clients been audited for paths that bypass the guard?
- [ ] Is a `SecurityViolationError` treated as a hard stop that trips the kill switch, never caught and retried?

## Boundary behaviour — verify each still holds

- [ ] Does a sandbox-mode call to the live gateway raise, **including** when the URL carries the word `paper` in a path or query parameter?
- [ ] Does a production-mode call to an unrecognised host (`api.<broker>.com.attacker.example`, or anything unrelated) raise, rather than passing because it merely is not a sandbox URL?
- [ ] Does an unregistered broker fail closed?
- [ ] Is a plaintext `http://` destination rejected?
- [ ] Is a URL carrying userinfo (`https://host@other.example/`) rejected?
- [ ] Is a key carrying the opposing environment's prefix rejected case-insensitively (`ak_live_…` as well as `AK_LIVE_…`)?

## Secret hygiene

- [ ] Do exception messages and log records omit the query string and fragment? (Signed Binance URLs carry `&signature=<hmac>`.)
- [ ] Is the API key absent from every log line and every raised message?
- [ ] Do error reporters, crash handlers, and log shippers receive only the redacted form?

## Ongoing review

- [ ] Has `iter_declared_endpoints()` been diffed against current vendor documentation since the last broker API change announcement?
- [ ] Is a stale allow-list understood to fail closed — rejecting legitimate traffic — so endpoint review is on the operational calendar, not left until an outage?
- [ ] Do the skill's unit tests pass (`python -m unittest discover -s skills/sandbox-credential-leakage-prevention/scripts`)?
