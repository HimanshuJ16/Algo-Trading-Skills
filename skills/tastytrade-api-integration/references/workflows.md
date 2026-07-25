# Deep Workflow Reference — tastytrade-api-integration

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Session Login**:
   - Issue POST to `/sessions` with username/email and password.
   - Extract `session-token` and store in `Authorization` header.

2. **OCC Option Ticker Formatting**:
   - Format 21-character OCC symbol string: 6-char left-padded ticker, 6-char YYMMDD expiration, 1-char C/P, 8-char strike * 1000.

3. **Multi-Leg Option Order Construction**:
   - Post to `/accounts/{account_number}/orders` with `legs` array, `order-type`, `price`, and `price-effect` (`Credit` / `Debit`).

4. **Account & Position Portfolio Tracking**:
   - Query `/accounts/{account_number}/positions` for active options & futures positions.

## Production Implementation Reference

- Reference code: `scripts/tastytrade_client.py` (`TastytradeClient`, `OptionLeg`, `TastytradeOrder`).
- Automated unit tests: `scripts/test_tastytrade_client.py`.
