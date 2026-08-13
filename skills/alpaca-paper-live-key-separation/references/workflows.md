# Deep Workflow Reference — alpaca-paper-live-key-separation

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Credential Prefix & Variable Separation:**
   - Define isolated environment variables: `ALPACA_PAPER_KEY_ID`, `ALPACA_PAPER_SECRET_KEY`, `ALPACA_LIVE_KEY_ID`, `ALPACA_LIVE_SECRET_KEY`.
   - Inspect key prefixes (`PK...` for paper vs `AK...` for live).
   - Validate that neither `key_id` nor `secret_key` is empty or whitespace.

2. **Base URL Endpoint Matching:**
   - Enforce positive URL matching: `PAPER` mode must target `https://paper-api.alpaca.markets`; `LIVE` mode must target `https://api.alpaca.markets`.
   - Any base URL that does not exactly match the expected endpoint is rejected.
   - Veto startup via `AlpacaEnvironmentManager.validate_config()` if paper credentials are paired with the live endpoint or vice versa.

3. **Explicit Live Execution Safety Gate:**
   - Require `ALLOW_LIVE_TRADING=true` environment flag for live execution mode.
   - Halt initialization immediately if live mode is specified without explicit environment confirmation.

4. **Account API Environment Probe:**
   - Query GET `/v2/account` and inspect the `is_paper` boolean.
   - Verify `status` is `ACTIVE`; any other status halts initialization.
   - Veto execution if the account probe's `is_paper` boolean conflicts with the configured environment mode.
   - **Fail-safe**: if `is_paper` is absent from the API response, treat the account as live (`is_paper=False`) to prevent a live account from silently passing paper-mode checks.

5. **Pre-Order Execution Veto Gate:**
   - Pass outbound order signals through `AlpacaEnvironmentManager.guard_order()` before executing trades.
   - If an account probe function is supplied, `guard_order()` performs a runtime re-probe in addition to static config validation.

## Failure Modes Observed in Production

- **Endpoint Misconfiguration:** Pairing paper credentials with `https://api.alpaca.markets`, exposing live capital to paper signals.
- **Missing Environment Confirmation:** Launching live trading without requiring an explicit `ALLOW_LIVE_TRADING=true` confirmation flag.
- **Unprobed Account Credentials:** Relying on local configuration without probing GET `/v2/account` to confirm account type.
- **Silent is_paper Default:** Assuming a missing `is_paper` field means paper, allowing a live account to pass paper-mode checks.

## Production Implementation Reference

- Reference code: `scripts/alpaca_env_guard.py` (`AlpacaEnvironmentManager`, `AlpacaConfig`, `TradingEnvironment`, `EnvironmentMismatchError`).
- Automated unit tests: `scripts/test_alpaca_env_guard.py`.
