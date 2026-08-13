# Promotion Workflow: Binance Futures Testnet to Mainnet

Endpoint-level procedure. Every citation for the endpoints and error codes named here is in
`references/standards.md`.

## Phase 0 — Establish that the testnet record is real

Before anything else, confirm the testnet phase actually ran on testnet. Check that the
testnet `base_url` resolves to a testnet host (`demo-fapi.binance.com`, `demo-dapi.binance.com`,
or the legacy `testnet.binancefuture.com`) and that its credentials differ from the mainnet
pair. `MainnetPromotionManager` enforces both — the credential check at construction
(`ValueError`) and the host check in `run_pre_flight_checks`.

If the testnet config was pointed at `fapi.binance.com`, stop. The strategy has been trading
live and the promotion decision is based on a false premise; treat it as an incident, not a
promotion.

## Phase 1 — Testnet exit criteria

- Strategy runs unattended for a sustained period without unhandled exceptions or manual
  restarts.
- Order lifecycle exercised end to end: new, partial fill, full fill, cancel, reject.
- Reconnect and resubscribe behaviour exercised (kill the websocket mid-session).
- Every `newClientOrderId` emitted matches `^[\.A-Z\:/a-z0-9_-]{1,36}$` and is unique.
- Known limitation: testnet books are thin and synthetic. Fill quality, slippage, funding,
  and queue position observed there are **not** evidence about mainnet. Treat them as
  functional tests, not performance results.

## Phase 2 — Credential and endpoint preparation

1. Generate a dedicated mainnet key with Futures trading enabled. Do not reuse the testnet key.
2. Apply an IP allowlist where the account permits it. Do not encode an assumed key-expiry
   window — Binance's permission-expiry rules have been revised and superseded.
3. Store testnet and mainnet credentials under distinctly named environment variables
   (e.g. `BINANCE_TESTNET_API_KEY` vs `BINANCE_MAINNET_API_KEY`). A single shared variable
   name is the mechanism by which most accidental live deployments happen.
4. Never log configuration objects that carry secrets. `ExchangeConfig.__repr__` here is
   overridden to redact; keep that property if you extend it.

## Phase 3 — Pre-flight gate (automated)

```python
import logging, os
from binance_futures_testnet_to_mainnet_promotion import (
    Environment, ExchangeConfig, MainnetPromotionManager, PromotionError,
)

logging.basicConfig(level=logging.INFO)

testnet = ExchangeConfig(
    api_key=os.environ["BINANCE_TESTNET_API_KEY"],
    api_secret=os.environ["BINANCE_TESTNET_API_SECRET"],
    base_url="https://demo-fapi.binance.com",
    environment=Environment.TESTNET,
)
mainnet = ExchangeConfig(
    api_key=os.environ["BINANCE_MAINNET_API_KEY"],
    api_secret=os.environ["BINANCE_MAINNET_API_SECRET"],
    base_url="https://fapi.binance.com",
    environment=Environment.MAINNET,
)

manager = MainnetPromotionManager(
    testnet, mainnet,
    max_leverage_limit=3,
    max_capital_risk_pct=0.01,
    # Operator decision, made outside the code path that wants to go live.
    allow_live_promotion=os.environ.get("BINANCE_ALLOW_MAINNET_PROMOTION") == "true",
)

try:
    active = manager.promote_to_mainnet({
        "leverage": 3,
        "capital_risk_pct": 0.01,
        "hard_stop_loss_enabled": True,
    })
except PromotionError:
    logger = logging.getLogger(__name__)
    logger.error("Promotion refused; strategy remains on testnet configuration.")
    raise
```

The gate enforces, in order:

1. `verify_api_connectivity(testnet_config)` — HTTPS + exact testnet host.
2. `verify_api_connectivity(mainnet_config)` — HTTPS + exact mainnet host.
3. `validate_risk_parameters(strategy_params)` — all keys present, finite, in range,
   integer leverage ≥ 1, `hard_stop_loss_enabled is True`.
4. `allow_live_promotion is True`.

Failure of any step raises `PromotionError` from `promote_to_mainnet`. All four are re-run on
every call, so a strategy cannot inherit a previous approval for different parameters.

If you run on COIN-M or a venue variant whose host is not in the defaults, pass an explicit
allowlist (`mainnet_hosts=frozenset({"papi.binance.com"})`) rather than removing the check.

## Phase 4 — Mainnet account reconciliation (manual/scripted, before the first order)

Run this against the mainnet host, in this order, because several of these calls are rejected
once positions or orders exist:

1. `GET /fapi/v1/ping` — connectivity, weight 1.
2. `GET /fapi/v1/time` — compare against local clock. Drift beyond `recvWindow`
   (default 5000 ms, max 60000 ms) produces `-1021` on every signed request.
3. `GET /fapi/v3/account` — confirm the key authenticates, and that balances and existing
   positions are what you expect. (`v2` equivalents carry a deprecation notice.)
4. `GET /fapi/v1/positionSide/dual` — compare against the testnet setting. Change it now if
   it differs; `POST` fails with `-4067` when open orders exist and `-4068` when a position exists.
5. `POST /fapi/v1/multiAssetsMargin` and `POST /fapi/v1/marginType` as required. `-4046` means
   the margin type is already what you asked for — treat it as success, not an error.
6. `GET /fapi/v1/leverageBracket` — read `initialLeverage`, `notionalFloor`, `notionalCap`
   per bracket for every symbol you trade. The maximum leverage is a function of notional
   tier and of account history, not a fixed number.
7. `POST /fapi/v1/leverage` — set it, then assert the returned `leverage` equals what you
   requested and that `maxNotionalValue` accommodates your intended position size.
   `-4028` means the value was refused outright.
8. `GET /fapi/v1/exchangeInfo` — refresh `LOT_SIZE` (`stepSize`, `minQty`), `MIN_NOTIONAL`,
   `PRICE_FILTER` (`tickSize`), `MARKET_LOT_SIZE` and symbol `status` for every traded symbol.
   Re-derive rounding from these values; do not carry over testnet-derived constants.
   Mismatches surface as `-2010` at order time.

## Phase 5 — Pilot

- First orders at minimum permitted notional, with the exchange-resident stop attached:
  `STOP_MARKET` with `closePosition=true` (and `workingType=MARK_PRICE` if you want the mark,
  not last trade, to trigger it). A stop that exists only in local strategy code does not
  protect the position when your host dies.
- Send an explicit `newClientOrderId`. If a submission times out, **query order state before
  resubmitting** — the documentation does not guarantee that reusing a client order id is a
  safe no-op, so a blind retry can double the position.
- Watch `X-MBX-USED-WEIGHT-*` and `X-MBX-ORDER-COUNT-*` headers. `429` is a warning; `418` is
  an IP ban scaling from 2 minutes to 3 days.
- Compare realized slippage, fees, and funding against the assumptions the strategy was sized
  on. This is the first honest measurement of them — testnet gave you none.

## Phase 6 — Scale

Increase allocation only after the pilot's realized costs are consistent with the model, and
only in steps small enough that a wrong assumption is survivable. Re-check
`GET /fapi/v1/leverageBracket` as notional grows: a larger position moves into a bracket with
lower `initialLeverage` and higher `maintMarginRatio`, so a size that was comfortably margined
at one tier can be near liquidation at the next.

## Rollback

Define the rollback before promoting, not after. Minimum: a documented procedure to flatten
positions with `reduceOnly`/`closePosition` orders, cancel all open orders, revoke the mainnet
key, and revert the deployment to the testnet configuration. Setting
`allow_live_promotion=False` prevents *future* promotions; it does not close existing
positions.
