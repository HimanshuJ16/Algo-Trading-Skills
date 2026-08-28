# Workflows for Sandbox Credential Leakage Prevention

## 1. Declare the environment explicitly

Source `TradingEnvironment` from deployment configuration — an environment variable,
a config file, a deployment manifest. Never infer it from the URL or the key you are
about to validate; that makes the guard agree with whatever it is handed.

```python
from credential_guard import CredentialEnvironmentGuard, TradingEnvironment

guard = CredentialEnvironmentGuard(TradingEnvironment.SANDBOX)
```

`allow_unknown_brokers` defaults to `False`. Leave it there. Setting it `True`
skips the boundary check for any broker without rules and logs a warning on every
call — a reviewed risk acceptance, recorded in code, not a convenience toggle.

## 2. Register rules for every broker in use

```python
from credential_guard import BrokerEnvironmentRules, CredentialEnvironmentGuard, TradingEnvironment

rules = {
    "myvenue": BrokerEnvironmentRules(
        broker_name="myvenue",
        sandbox_endpoints=["sim.myvenue.example"],
        production_endpoints=["api.myvenue.example", "api2.myvenue.example"],
        # Omit prefixes entirely unless the venue documents a scheme.
    ),
}
guard = CredentialEnvironmentGuard(TradingEnvironment.PRODUCTION, custom_rules=rules)
```

`custom_rules` **replaces** `BROKER_RULES`; it does not merge with it. To extend the
shipped defaults, copy them first:

```python
from credential_guard import BROKER_RULES
rules = {**BROKER_RULES, "myvenue": my_rule}
```

Endpoints accept `"host"` or `"host/path/prefix"` shorthand. Use a path prefix only
when the venue genuinely separates environments by path (Saxo does; Alpaca and
Binance do not) — an unnecessary prefix will reject legitimate endpoints under other
paths on the same host.

A `BrokerEnvironmentRules` with no endpoints at all raises `ValueError` at
construction: it would reject every request, which is a configuration bug that
should surface at startup rather than at the first order.

## 3. Call the guard immediately before every outbound request

```python
guard.validate_request_boundary(
    broker_name="alpaca",
    api_key=key_id,
    target_url="https://api.alpaca.markets/v2/orders",
)
response = session.post(url, headers=headers, json=payload)
```

Put the call inside the single HTTP wrapper every broker request passes through, not
at each call site. A guard that some code paths skip protects only the paths that
remembered it — and health checks, retry helpers, and vendored SDK clients are
exactly the paths that forget.

## 4. What the guard checks, in order

1. **Input validation** — `broker_name`, `api_key`, and `target_url` must be
   non-empty strings. An empty credential raises `ValueError`; it is a configuration
   failure, not a security event, and conflating the two hides both.
2. **URL structure** — scheme must be `https`; no userinfo; hostname present; port
   absent or 443. Each failure raises `SecurityViolationError`.
3. **Broker lookup** — no rules and `allow_unknown_brokers=False` ⟹ violation.
4. **Key prefix** — opposing environment's prefix ⟹ violation; unrecognised prefix
   ⟹ warning only (see `references/standards.md` for why).
5. **Endpoint allow-list** — exact hostname plus normalised path prefix must match an
   endpoint declared for this environment. A match against the *opposing*
   environment's list produces the explicit cross-environment message; anything else
   reports that the destination is unrecognised.

## 5. Handle the violation as a hard stop

```python
from credential_guard import SecurityViolationError

try:
    guard.validate_request_boundary(broker, key_id, url)
except SecurityViolationError:
    logger.critical("Environment boundary violation; halting order flow.", exc_info=True)
    kill_switch.trip()
    raise
```

Do not catch and retry. A boundary violation is a configuration or deployment defect;
retrying re-attempts the same misrouted request. Escalate to the kill switch
(`kill-switch-and-drawdown-circuit-breakers`) and stop the strategy — a process that
cannot tell which environment it is in should not be placing orders in either.

Exception messages are safe to log: URLs are stripped of userinfo, query, and
fragment, and the API key is never included — only the prefix that matched.

## 6. Review the allow-list on a schedule

```python
from credential_guard import iter_declared_endpoints

for broker, environment, endpoint in iter_declared_endpoints():
    print(f"{broker:10} {environment:10} {endpoint}")
```

Diff the output against current vendor documentation whenever a broker announces API
changes, and at least at each recurring infrastructure review. A stale allow-list
fails closed — legitimate traffic starts being rejected — which is the correct
failure direction but still an outage. `sandbox-vs-production-endpoint-drift` covers
the wider parity audit between the two environments.
