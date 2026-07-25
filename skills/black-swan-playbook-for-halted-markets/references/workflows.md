# Deep Workflow Reference — black-swan-playbook-for-halted-markets

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Ingest Exchange Halt Signal**: Detect `HALTED_LULD`, `HALTED_CIRCUIT_BREAKER`, or `RESUME_AUCTION`.
2. **Cancel Working Orders**: Issue batch order cancellations for all open pending orders on halted symbol.
3. **Deploy Correlated Proxy Hedge**: Calculate beta-adjusted hedge quantity and submit short/long proxy ETF order ($QQQ/SPY$).
4. **Prepare Auction Resumption**: Monitor `RESUME_AUCTION` feed and recalculate post-halt clearing prices before re-entry.

## Production Implementation Reference

- Reference code: `scripts/halted_market_engine.py` (`BlackSwanHaltedMarketEngine`, `HaltedMarketReport`, `MarketStatus`).
- Automated unit tests: `scripts/test_halted_market_engine.py`.
