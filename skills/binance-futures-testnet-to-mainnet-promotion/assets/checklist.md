# Binance Futures Mainnet Promotion Checklist

Sign-off gate. Every unchecked box is a reason not to promote.

## Environment integrity

- [ ] Testnet `base_url` host is a testnet host (`demo-fapi.binance.com`,
      `demo-dapi.binance.com`, or legacy `testnet.binancefuture.com`) — confirming the
      testnet record was not produced against live capital.
- [ ] Mainnet `base_url` host is `fapi.binance.com` (USDⓈ-M) or `dapi.binance.com` (COIN-M).
- [ ] Both URLs use HTTPS and are matched by exact hostname, not prefix/suffix.
- [ ] Testnet and mainnet `api_key` values differ; `api_secret` values differ.
- [ ] Credentials live in distinctly named environment variables, not one shared name.
- [ ] Mainnet key has Futures trading permission; IP allowlist applied where supported.
- [ ] No code path logs or serializes a config object that could disclose `api_secret`.

## Strategy readiness

- [ ] Out-of-sample backtesting completed.
- [ ] Slippage, taker/maker fees, and funding modelled — and it is understood that testnet
      provided no valid evidence for any of them.
- [ ] Testnet run stable and unattended for the agreed period, covering new / partial fill /
      full fill / cancel / reject and a websocket reconnect.
- [ ] Every `newClientOrderId` matches `^[\.A-Z\:/a-z0-9_-]{1,36}$` and is unique.

## Risk configuration

- [ ] `leverage`, `capital_risk_pct`, and `hard_stop_loss_enabled` are all explicitly present —
      no key relies on a default.
- [ ] `leverage` is an integer ≥ 1 and within the pilot ceiling.
- [ ] `capital_risk_pct` is a fraction (0.01 = 1%), finite, > 0, within the ceiling.
- [ ] `hard_stop_loss_enabled` is the boolean `True`, not a truthy string.
- [ ] Exchange-resident stop confirmed: `STOP_MARKET` with `closePosition=true`, so the
      position is protected if the strategy host becomes unreachable.
- [ ] `allow_live_promotion` set deliberately by a named operator, from a deployment flag —
      not hardcoded `True` in the strategy.
- [ ] Pre-flight gate executed and passed on the exact config being deployed.

## Mainnet account reconciliation (before the first order)

- [ ] `GET /fapi/v1/ping` succeeds against the mainnet host.
- [ ] Clock skew vs `GET /fapi/v1/time` is comfortably inside `recvWindow` (default 5000 ms).
- [ ] `GET /fapi/v3/account` authenticates; balances and existing positions are as expected.
- [ ] Position mode (`/fapi/v1/positionSide/dual`) matches the testnet setting — reconciled
      *before* any position or open order exists (`-4067` / `-4068` otherwise).
- [ ] Multi-assets margin mode and per-symbol margin type set (`-4046` = already set = OK).
- [ ] `GET /fapi/v1/leverageBracket` reviewed; intended notional sits in a bracket that
      permits the configured leverage.
- [ ] `POST /fapi/v1/leverage` response read back and matches the requested value.
- [ ] `GET /fapi/v1/exchangeInfo` refreshed on mainnet; `stepSize`, `minQty`, `MIN_NOTIONAL`,
      `tickSize` and symbol `status` re-derived rather than carried over from testnet.

## Pilot and rollback

- [ ] First live orders sized at minimum permitted notional.
- [ ] Ambiguous-submission procedure agreed: query order state before any retry; never blindly
      resubmit a timed-out `POST /fapi/v1/order`.
- [ ] Rate-limit headers (`X-MBX-USED-WEIGHT-*`, `X-MBX-ORDER-COUNT-*`) monitored; `429`/`418`
      handling in place.
- [ ] Rollback procedure written down and tested: flatten with `reduceOnly`/`closePosition`,
      cancel all open orders, revoke mainnet key, revert to testnet config.
- [ ] Named owner on call for the pilot window.
