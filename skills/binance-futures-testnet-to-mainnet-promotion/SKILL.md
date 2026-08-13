---
name: binance-futures-testnet-to-mainnet-promotion
description: Use when promoting a Binance Futures strategy from testnet to mainnet,
  to bind each base URL to its declared environment, keep testnet and mainnet API
  credentials strictly separate, fail closed on missing or malformed risk limits,
  and require explicit operator authorization before real leveraged capital is at risk
domain: global-market-integration
subdomain: exchanges
tags:
- binance-futures
- deployment
- risk-management
- environment-segregation
- live-capital-guard
brokers_frameworks:
- Binance USDⓈ-M Futures API
- Binance COIN-M Futures API
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

# Binance Futures Testnet to Mainnet Promotion

## When to Use

Invoke this when a strategy that has been running on the Binance Futures testnet is about
to route orders against a mainnet base URL. The promotion step itself is the hazard: the
same code, pointed at a different host with different credentials, moves from fake balances
to real leveraged capital with liquidation risk. This skill supplies the gate that must pass
before an order router is handed a mainnet `ExchangeConfig`, plus the account-level
reconciliation steps that testnet cannot exercise.

Use it for both USDⓈ-M (`fapi`) and COIN-M (`dapi`) futures.

## When NOT to Use

- **Spot or Margin promotion**: Binance spot uses different hosts (`api.binance.com`) and a
  different testnet; the host allowlist and the leverage/position-mode checks here do not apply.
- **Brokers with a single endpoint for both environments**: If environments are distinguished
  only by credentials, the host-binding logic is inapplicable — see `alpaca-paper-live-key-separation`
  for the credential-prefix variant of this pattern.
- **Backtesting or offline simulation**: No live endpoint is involved, so environment
  segregation is irrelevant. Use `demo-account-realism-gap-assessment` to judge whether the
  testnet record is meaningful at all.
- **As a substitute for a general go-live decision**: This gate checks environment wiring and
  configured risk limits. It does not judge whether the strategy's *performance* justifies live
  capital — that is `paper-to-live-promotion-checklist`.
- **As a runtime risk control**: This runs once at promotion. Continuous drawdown and exposure
  enforcement belongs to `kill-switch-and-drawdown-circuit-breakers`.

## Prerequisites

- Python 3.10+ (standard library only; this module performs no network I/O).
- Separate Binance Futures testnet and mainnet API keys, held in distinctly named
  environment variables. Testnet keys are issued from a separate registration flow and are
  not valid on mainnet — if the same value appears in both configs, one leg is wrong.
- Mainnet key with Futures trading permission enabled and, where the account allows it, an
  IP allowlist. Binance's API-key permission and expiry rules have changed more than once;
  confirm the current rules in Binance's API management docs rather than assuming.
- A testnet track record produced against a *testnet* host (verify this — it is the first
  thing the gate checks).

## Workflow

1. **Build both configurations**: Construct `ExchangeConfig` for testnet and mainnet.
   `MainnetPromotionManager.__init__` rejects, as `ValueError`, any config whose
   `environment` enum is wrong, any pair that shares an `api_key` or `api_secret`, and any
   nonsensical risk ceiling (e.g. `max_capital_risk_pct=2`, the percent-vs-fraction slip).

2. **Bind each URL to its environment**: `verify_api_connectivity` requires HTTPS and an
   *exact* hostname match against the allowlist for the declared environment. Both legs are
   checked — a TESTNET-labelled config pointing at `fapi.binance.com` means the "paper"
   track record was produced with real orders, so it invalidates the promotion rather than
   merely warning. Exact matching is deliberate: a `startswith`/`endswith` comparison accepts
   `https://fapi.binance.com.attacker.example`.

3. **Validate risk parameters, failing closed**: `validate_risk_parameters` rejects a missing
   key rather than defaulting it, rejects NaN/Inf, rejects non-integer leverage (Binance
   accepts integer leverage only), and requires `hard_stop_loss_enabled` to be the boolean
   `True` — not any truthy value, because a config loader yielding the string `"false"` is truthy.

4. **Require explicit authorization**: `allow_live_promotion` defaults to `False`. Wire it
   from an operator-controlled deployment flag at the call site
   (`allow_live_promotion=os.environ.get("BINANCE_ALLOW_MAINNET_PROMOTION") == "true"`).
   The module deliberately does not read the environment itself, so the decision stays
   explicit and the gate stays deterministic under test.

5. **Reconcile mainnet account state before the first order** — this is the part testnet
   cannot cover, because these are per-account, per-environment settings that do not travel
   with your code:
   - Position mode: `GET /fapi/v1/positionSide/dual`. If it disagrees with testnet, change it
     *before* opening anything — `POST /fapi/v1/positionSide/dual` is rejected with `-4067`
     when open orders exist and `-4068` when a position exists.
   - Multi-assets margin mode (`/fapi/v1/multiAssetsMargin`) and per-symbol margin type
     (`/fapi/v1/marginType`, which returns `-4046` when already set to the requested value).
   - Leverage: set it with `POST /fapi/v1/leverage` and **read the response back**. Check the
     permitted brackets via `GET /fapi/v1/leverageBracket`; the leverage your testnet config
     assumed may exceed what this account and notional tier allow, and Binance has applied
     lower caps to newly opened futures accounts.
   - Symbol filters: re-read `GET /fapi/v1/exchangeInfo` on mainnet. `LOT_SIZE` (`stepSize`,
     `minQty`), `MIN_NOTIONAL`, `PRICE_FILTER` (`tickSize`) and symbol availability are not
     guaranteed to match testnet, so quantities that were accepted on testnet can be
     rejected live.

6. **Promote**: Call `promote_to_mainnet(strategy_params)`. Every call re-runs the full
   pre-flight sequence; a prior success never short-circuits a later parameter set.

7. **Pilot, then scale**: Run minimum-notional size first and compare realized slippage,
   funding, and fees against the testnet assumptions before increasing allocation. See
   `incremental-capital-deployment-for-new-strategies`.

> Full step-by-step procedure with endpoint-level detail: see `references/workflows.md`.
> Cited Binance API surface for this skill: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating any `https://` URL as safe**: HTTPS says nothing about *which* venue you reached.
  Bind the host to the declared environment and compare hostnames exactly.
- **Reusing one credential pair across both configs**: If the shared value is the mainnet key,
  the "testnet" phase was live trading. If it is the testnet key, mainnet auth simply fails —
  the harmless direction, which is why the dangerous direction goes unnoticed.
- **Defaulting a missing risk limit**: `params.get("leverage", 0)` turns a typo'd key into a
  pass. On a promotion gate, absent means reject.
- **Comparing against NaN**: `float("nan") > max_leverage` is `False`, so a NaN risk parameter
  passes a naive bounds check. Test finiteness explicitly.
- **Truthiness checks on safety flags**: the string `"false"` from an env var or YAML loader is
  truthy and will silently disable a stop-loss requirement.
- **Treating "already promoted" as idempotent**: returning the mainnet config on a repeat call
  without re-validating lets a later, over-leveraged parameter set inherit an earlier approval.
- **Assuming account settings carry over**: position mode, multi-assets mode, margin type and
  leverage are per-account and per-environment. Changing position mode after you already hold
  a position or open order fails (`-4067`/`-4068`), so reconcile before the first order.
- **Assuming testnet symbol filters match mainnet**: differing `stepSize`/`minNotional` produce
  live `-2010` rejections for sizes that worked on testnet.
- **Logging config objects**: a plain dataclass `repr` prints `api_secret` verbatim into logs
  and tracebacks. `ExchangeConfig` here redacts it; do the same for any config you add.
- **Trusting the testnet fill model**: Binance testnet order books are thin and synthetic.
  Slippage, partial fills, and funding observed there are not evidence about mainnet.
- **Retrying an ambiguous order on the first live orders**: a timed-out `POST /fapi/v1/order`
  may already have been accepted. Send a client-supplied `newClientOrderId`
  (`^[\.A-Z\:/a-z0-9_-]{1,36}$`) and reconcile before resubmitting — see
  `order-placement-idempotency`.

## Verification

- Run the unit suite: `python -m unittest discover -s skills/binance-futures-testnet-to-mainnet-promotion/scripts`.
- Point a TESTNET-labelled config at `https://fapi.binance.com` and confirm
  `run_pre_flight_checks` returns `False`.
- Set `base_url` to `https://fapi.binance.com.attacker.example` and confirm rejection.
- Construct a manager with the same `api_key` in both configs and confirm `ValueError`.
- Omit `leverage` from `strategy_params` and confirm rejection; repeat with
  `capital_risk_pct=float("nan")` and with `hard_stop_loss_enabled="false"`.
- Leave `allow_live_promotion` at its default and confirm `promote_to_mainnet` raises
  `PromotionError`.
- Promote successfully, then call again with `leverage=50` and confirm `PromotionError`.
- Confirm `repr(config)` and `str(config)` contain no secret material.
- Against the live account: confirm `POST /fapi/v1/leverage` echoes back the leverage you
  requested, and that `GET /fapi/v1/exchangeInfo` filters for every traded symbol match the
  quantities your sizing logic emits.

## Related Skills

- `paper-to-live-promotion-checklist`
- `alpaca-paper-live-key-separation`
- `demo-account-realism-gap-assessment`
- `sandbox-vs-production-endpoint-drift`
- `perpetual-futures-funding-rate-handling`
- `kill-switch-and-drawdown-circuit-breakers`
- `order-placement-idempotency`
- `incremental-capital-deployment-for-new-strategies`
