# Deep Workflow Reference — saxo-bank-openapi-integration

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **OAuth2 Bearer Authentication**:
   - Obtain Bearer access token via Saxo OAuth2 flow.
   - Configure gateway (`https://gateway.saxobank.com/sim/openapi` for SIM or `/openapi` for LIVE).

2. **Instrument Search & UIC Resolution**:
   - Query GET `/ref/v1/instruments?Keywords={query}&AssetTypes={AssetType}`.
   - Extract numeric Saxo Universal Instrument Code (`Uic`).

3. **Multi-Asset Order Placement**:
   - Post to `/trade/v1/orders` with `AccountKey`, `Uic`, `AssetType`, `Amount`, `OrderType`, and `OrderDuration`.

4. **Portfolio Position & Margin Tracking**:
   - Query GET `/port/v1/positions?AccountKey={AccountKey}` to ingest live multi-asset position states.

## Production Implementation Reference

- Reference code: `scripts/saxo_client.py` (`SaxoBankOpenAPIClient`, `SaxoInstrument`, `SaxoOrder`).
- Automated unit tests: `scripts/test_saxo_client.py`.
