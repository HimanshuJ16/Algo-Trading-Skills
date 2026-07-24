# Deep Workflow Reference — alpaca-paper-live-key-separation

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Credential Prefix & Variable Separation:**
   - Define isolated environment variables: `ALPACA_PAPER_KEY_ID`, `ALPACA_PAPER_SECRET_KEY`, `ALPACA_LIVE_KEY_ID`, `ALPACA_LIVE_SECRET_KEY`.
   - Inspect key prefixes (`PK...` for paper vs `AK...` for live).

2. **Base URL Endpoint Matching:**
   - Enforce URL pairing: `PAPER` mode must target `https://paper-api.alpaca.markets`; `LIVE` mode must target `https://api.alpaca.markets`.
   - Veto startup via `AlpacaEnvironmentManager.validate_config()` if paper credentials are paired with the live endpoint or vice versa.

3. **Explicit Live Execution Safety Gate:**
   - Require `ALLOW_LIVE_TRADING=true` environment flag for live execution mode.
   - Halt initialization immediately if live mode is specified without explicit environment confirmation.

4. **Account API Environment Probe:**
   - Query GET `/v2/account` and inspect the `is_paper` boolean.
   - Veto execution if the account probe's `is_paper` boolean conflicts with the configured environment mode.

5. **Pre-Order Execution Veto Gate:**
   - Pass outbound order signals through `AlpacaEnvironmentManager.guard_order()` before executing trades.

## Failure Modes Observed in Production

- **Endpoint Misconfiguration:** Pairing paper credentials with `https://api.alpaca.markets`, exposing live capital to paper signals.
- **Missing Environment Confirmation:** Launching live trading without requiring an explicit `ALLOW_LIVE_TRADING=true` confirmation flag.
- **Unprobed Account Credentials:** Relying on local configuration without probing GET `/v2/account` to confirm account type.

## Production Implementation Reference

- Reference code: `scripts/alpaca_env_guard.py` (`AlpacaEnvironmentManager`, `AlpacaConfig`, `TradingEnvironment`).
- Automated unit tests: `scripts/test_alpaca_env_guard.py`.
