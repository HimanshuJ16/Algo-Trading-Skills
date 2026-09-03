# Deep Workflow Reference — alpaca-paper-live-key-separation

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Environment Mode Normalisation:**
   - Coerce the configured environment into a `TradingEnvironment` member before any comparison, accepting the enum or its plain-string form (config loaded from YAML or `.env` supplies strings).
   - Raise on anything unrecognised. A mode that matches neither `PAPER` nor `LIVE` must never reach the end of the validator: a bare `if/elif` with no `else` returns success for a third value, silently authorising a live order.

2. **Credential Prefix & Variable Separation:**
   - Define isolated environment variables: `ALPACA_PAPER_KEY_ID`, `ALPACA_PAPER_SECRET_KEY`, `ALPACA_LIVE_KEY_ID`, `ALPACA_LIVE_SECRET_KEY`.
   - Reject a `key_id` bearing the opposite environment's prefix (`AK…` in paper mode, `PK…` in live mode).
   - Do **not** require a positive prefix match. The `PK`/`AK` convention is not documented by Alpaca (see `standards.md`), so an unrecognised format — e.g. a broker-API `CK…` key — must pass rather than be wrongly refused.
   - Validate that neither `key_id` nor `secret_key` is empty or whitespace.

3. **Base URL Endpoint Matching:**
   - Enforce positive URL matching: `PAPER` mode must target `https://paper-api.alpaca.markets`; `LIVE` mode must target `https://api.alpaca.markets`.
   - Compare the *whole* normalised URL against the allow-list — strip whitespace, drop a trailing slash, and casefold, since hostnames are case-insensitive. Never use `in` or `startswith`: a look-alike host such as `https://api.alpaca.markets.attacker.example` passes a prefix test.
   - Veto startup via `AlpacaEnvironmentManager.validate_config()` if paper credentials are paired with the live endpoint or vice versa.

4. **Explicit Live Execution Safety Gate:**
   - Require `ALLOW_LIVE_TRADING=true` environment flag for live execution mode; strip surrounding whitespace before comparing so a `.env` loader's trailing newline does not block a legitimate deployment.
   - Halt initialization immediately if live mode is specified without explicit environment confirmation.
   - Log a WARNING whenever live trading is authorised, so the paper→live transition appears in the operational record.

5. **Account API Environment Probe:**
   - Query GET `/v2/account`. Reject a response that is not a mapping — an SDK returning an entity object or `None` must produce the documented `EnvironmentMismatchError`, not an `AttributeError` that escapes the caller's handler.
   - Verify tradability: `status` must be `ACTIVE`, or `PAPER_ONLY` when in paper mode. A *missing* or blank `status` is a veto — defaulting it to `ACTIVE` turns a truncated payload into an apparently healthy account.
   - Veto if `trading_blocked`, `account_blocked`, or `trade_suspended_by_user` is set.
   - **Interpret booleans loosely, fail closed.** Loosely-typed adapters deliver `"true"`/`"false"` as strings; a strict `is True` test silently discards them, so an explicit negative signal (`is_paper: "false"`, `trading_blocked: "true"`) reads as *absent* and is waved through. Accept the string forms, treat a blocking flag that is present but unreadable as set, and treat a present-but-uninterpretable `is_paper` as a corrupt discriminator that vetoes rather than falling back.
   - **Resolve the environment from fields that exist.** Alpaca's account payload has no `is_paper` key (verified against the API schema and the official `alpaca-py` model). Precedence: an `is_paper` bool if an SDK wrapper injects one → `status == "PAPER_ONLY"` → `account_number` beginning `PA`. The last two are unofficial conventions and are used only to identify *paper*.
   - If nothing resolves, record the environment as **undeterminable** and rely on the already-verified base URL. Treating "undeterminable" as live vetoes every real paper deployment, since the field is never present; treating it as paper would wave a live account through. Operators who supply an adapter that does inject a discriminator can set `require_environment_evidence=True` to make the unresolved case fail closed.

6. **Pre-Order Execution Veto Gate:**
   - Pass outbound order signals through `AlpacaEnvironmentManager.guard_order()` before executing trades.
   - Validate the order at the gate: `symbol` non-empty, `qty` a finite positive real (rejecting `bool`, `NaN`, and `inf`), `side` in `buy`/`sell`.
   - Validate configuration *before* order parameters, so a live-mode veto still fires on a malformed order rather than being masked by a `ValueError`.
   - If an account probe function is supplied, `guard_order()` performs a runtime re-probe in addition to static config validation.

## Failure Modes Observed in Production

- **Endpoint Misconfiguration:** Pairing paper credentials with `https://api.alpaca.markets`, exposing live capital to paper signals.
- **Missing Environment Confirmation:** Launching live trading without requiring an explicit `ALLOW_LIVE_TRADING=true` confirmation flag.
- **Unprobed Account Credentials:** Relying on local configuration without probing GET `/v2/account` to confirm the account is tradable.
- **Phantom-Field Guard:** Writing the probe against an `is_paper` field the API does not return, so the check reads `None` on every call and silently degrades to whatever the "missing" branch does.
- **Fall-Through Authorisation:** An unrecognised environment value matching neither branch, so the validator returns success without ever consulting `ALLOW_LIVE_TRADING`.
- **Fail-Open Status Default:** Defaulting a missing `status` to `ACTIVE`, so an error-shaped payload reads as a healthy account.
- **Blocked-Account Surprise:** Submitting into an account with `trading_blocked=true` and diagnosing the resulting broker rejection as a strategy bug.

## Production Implementation Reference

- Reference code: `scripts/alpaca_env_guard.py` (`AlpacaEnvironmentManager`, `AlpacaConfig`, `TradingEnvironment`, `EnvironmentMismatchError`).
- Automated unit tests: `scripts/test_alpaca_env_guard.py`.
