---
name: crypto-exchange-api-integration
description: Use when integrating a crypto exchange (Binance, Coinbase Advanced Trade,
  Kraken) for spot or derivatives trading, where 24/7 markets, structurally different
  rate-limit models, and exchange-specific order semantics break assumptions carried
  over from equities broker integration
domain: algorithmic-trading
subdomain: global-market-integration
tags:
- global-market-integration
- binance-spot-futures-api
- coinbase-advanced-trade-api
- kraken-rest-websocket-v2
brokers_frameworks:
- Binance Spot/Futures API
- Coinbase Advanced Trade API
- Kraken REST/WebSocket v2
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when a bot needs to place orders or consume market data on a crypto exchange rather than a traditional equities/derivatives broker. Crypto exchanges differ from the equities-broker patterns covered elsewhere in this repo in three structural ways that matter for correctness: markets run 24/7 with no session close to reset daily state against, rate limits use models that differ *between exchanges* (Binance meters weight per fixed clock window, Kraken runs a per-API-key decaying counter, Coinbase Advanced Trade throttles requests per second), and order semantics (time-in-force, self-trade prevention, post-only) vary meaningfully exchange to exchange — and even between spot and futures on the *same* exchange.

## When NOT to Use

- **As a portable multi-exchange rate limiter.** The three exchanges named here use three incompatible limiting models. A weight-per-window limiter cannot express Kraken's decaying counter or Coinbase's requests-per-second throttle; forcing one model onto all three produces a limiter that is wrong in both directions. `scripts/weight_rate_limiter.py` ships the two models it can implement from unambiguous published figures and deliberately ships no Coinbase preset.
- **As a source of current rate limits.** Published limits change — Binance Spot's REQUEST_WEIGHT limit was 1,200/min until 2023-08-25, when it became 6,000/min. Any constant in this skill is a starting default to confirm against the exchange, not an authority. Read Binance's live limits from `GET /api/v3/exchangeInfo`.
- **As a complete exchange client.** There is no auth/signing, no WebSocket transport, no order-state machine here. Signing and key custody belong to `crypto-wallet-key-custody-security`; reconnect semantics to `websocket-reconnect-without-duplicate-subscriptions`.
- **For non-Binance order payloads.** `CryptoOrderPayload` renders Binance REST parameters specifically. Coinbase and Kraken use different field names and order grammars entirely.

## Prerequisites

- API key + secret with the correct permission scopes (read, trade, withdraw — request only what the bot needs; never grant withdrawal permission to a key used for trading logic)
- The exchange's *current* rate-limit model and figures, read from its own documentation — not just a number, but which model it is (weight per window / decaying counter / requests per second) and whether it is metered per IP or per API key. Binance meters REQUEST_WEIGHT per IP; Kraken's counter is per API key.
- The set of self-trade-prevention modes the specific symbol accepts, from `allowedSelfTradePreventionModes` in `GET /api/v3/exchangeInfo` — allowed modes are per-symbol, so a mode that works on one pair can be rejected on another
- IP allowlisting configured if the exchange supports it (reduces blast radius if a key leaks)

## Workflow

1. **Define your own reset boundary.** Because there is no daily market close, any logic that assumes a natural reset point (e.g. daily P&L resetting at session close, as in `kill-switch-and-drawdown-circuit-breakers`) must define its own window explicitly — a rolling 24h window (`Rolling24hPnLTracker`) or a fixed UTC daily boundary — rather than relying on an exchange-provided session boundary that does not exist.

2. **Match the rate limiter to the exchange's actual model, and meter at the right scope.**
   - *Binance* — REQUEST_WEIGHT per IP, per **fixed clock window**: the 1-minute counter resets at :00, it does not slide. Spot is 6,000 weight/min (since 2023-08-25); USD-M futures is 2,400 weight/min. Endpoint weights differ, so an order-book depth call can cost as much as a dozen ticker calls. Use `WeightRateLimiter`, and prefer `CryptoExchangeRateLimiter.binance_from_exchange_info()` over the built-in constants.
   - *Kraken* — a per-**API-key** decaying counter, not a window: the counter rises per call and decays continuously at a tier-dependent rate (Starter 15 max / −0.33 per sec, Intermediate 20 / −0.5, Pro 20 / −1). Most calls cost 1; ledger and trade-history calls cost 2. `AddOrder`/`CancelOrder` run on a *separate* Kraken limiter that `KrakenDecayCounterLimiter` does not model.
   - *Coinbase Advanced Trade* — requests per second, throttled by IP, with usage reported in `CB-RATE-LIMIT-*` response headers. Published figures differ between Coinbase pages, so read the current one and register a limiter explicitly; this skill ships no Coinbase preset rather than a guessed one.
   - Never let a limiter registry auto-create a budget for an unrecognised namespace. A typo that silently receives an invented limit looks exactly like working code until the ban.

3. **Reconcile against the exchange's own counter, in both directions.** Binance echoes `X-MBX-USED-WEIGHT-1M`; adopt it as authoritative after every response, since other processes sharing the IP consume the same budget. Sync must be able to move the local counter *down* as well as up — a counter that only ratchets upward never follows the exchange's reset at the window boundary and pins the limiter at its ceiling, silently halting trading.

4. **Choose the self-trade-prevention mode explicitly, then check the symbol accepts it.** Binance offers `EXPIRE_MAKER`, `EXPIRE_TAKER`, `EXPIRE_BOTH`, `DECREMENT` and `NONE`; omitting the parameter yields `NONE`. A market-making bot and a directional bot want different behavior here, so `CryptoOrderPayload` makes `stp_mode` a required argument — pass `NONE` deliberately if that is the intent. Confirm the choice against the symbol's `allowedSelfTradePreventionModes` before sending.

5. **Express post-only the way the target market actually spells it.** There is no `execInst` parameter on Binance — that is BitMEX syntax, and sending it does not make an order post-only; it just leaves a supposedly maker-only order free to cross the spread and pay taker fees. On Binance **spot**, post-only is `type=LIMIT_MAKER` with no `timeInForce`. On **USD-M futures**, `LIMIT_MAKER` does not exist at all; post-only is `type=LIMIT` with `timeInForce=GTX`. Sending `timeInForce` where it is not required is rejected outright ("Parameter 'timeInForce' sent when not required"). `CryptoOrderPayload` requires `market_type` for exactly this reason.

6. **Prefer the WebSocket user-data stream for fills, but reconcile over REST on reconnect** exactly as in `websocket-reconnect-without-duplicate-subscriptions` — crypto user-data streams are as prone to silent gaps around a reconnect as any other WebSocket feed.

7. **Treat maintenance windows as a distinct failure class.** Even "24/7" exchanges have scheduled maintenance and unscheduled outages during which order placement is rejected or the socket stays down for an extended period. Back off with jitter; do not treat an extended outage as a transient blip. On Binance a 429 carries `Retry-After` and must be honoured, and continuing to send after 429s escalates to an automated IP ban (HTTP 418) that scales from 2 minutes to 3 days for repeat offenders.

8. **Keep spot and futures fully separate.** Binance Spot and Binance USD-M Futures are distinct API surfaces with separate rate-limit budgets, separate WebSocket endpoints, separate balances, and — as above — different order grammars. Never assume a unified budget or balance view across them.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Porting a request-count limiter from equities work.** Weight-based accounting means the bot hits the limit well before a naive request counter suggests it should.
- **Modelling Binance's counter as a sliding window.** It resets on the clock minute. A sliding local window cannot be reconciled with `X-MBX-USED-WEIGHT-1M`, and syncing one to the other is what produces the ratchet deadlock below.
- **A header sync that only ratchets upward.** The local counter never follows the exchange's reset, climbs to the ceiling, and the bot stops sending — an availability outage caused by the rate limiter itself, not the exchange.
- **Auto-creating a limiter for an unknown namespace.** A mistyped namespace gets a fabricated budget and runs unthrottled against the real limit.
- **Assuming `execInst: PostOnly` works on Binance.** It is not a Binance parameter. The order is accepted without post-only protection and can cross the spread as a taker.
- **Assuming `LIMIT_MAKER` works on futures.** USD-M futures does not support that type; post-only there is `LIMIT` + `GTX`.
- **Sending `timeInForce` with `MARKET` or `LIMIT_MAKER`.** Binance rejects parameters sent when not required.
- **Accepting an STP default nobody chose**, or picking a mode without checking the symbol's `allowedSelfTradePreventionModes`.
- **Assuming a "daily" risk reset happens** at a session boundary that does not exist on a 24/7 market, silently carrying stale daily risk state forward.
- **Retrying through a maintenance window** aggressively enough to escalate a 429 into an 418 IP ban.
- **Assuming spot and futures share balances or rate limits** when the exchange treats them as fully separate surfaces.
- **Treating a P&L timestamp of `0.0` as "not supplied."** A falsy-check on an epoch timestamp stamps an ancient record as current and corrupts the rolling risk window.

## Verification

- Confirm the limiter's weight accounting matches the exchange's documented weight table for the endpoints the bot actually calls, by comparing the local counter against the exchange's returned usage header after each response.
- Confirm the limiter's counter returns to zero at the clock-window boundary after a header sync, and that a sync reporting a *lower* value moves the local counter down. A limiter that cannot do this will eventually deadlock.
- Confirm `get_limiter()` on an unregistered namespace raises rather than returning a limiter.
- Confirm a spot post-only order renders `type=LIMIT_MAKER` with no `timeInForce` and no `execInst`, and that the futures equivalent renders `type=LIMIT` with `timeInForce=GTX`.
- Simulate a maintenance-window disconnect (or test against the exchange's testnet during an announced maintenance window) and confirm backoff does not escalate into rapid reconnect attempts.
- Confirm a self-trade scenario resolves according to the explicitly chosen STP mode, on a symbol whose `allowedSelfTradePreventionModes` includes it.
- Run `python scripts/test_weight_rate_limiter.py` (or `python -m unittest discover -s skills/crypto-exchange-api-integration/scripts`) and confirm a 100% pass rate.

## Related Skills

- `multi-broker-rate-limit-handling`
- `websocket-reconnect-without-duplicate-subscriptions`
- `crypto-wallet-key-custody-security`
- `multi-timezone-session-scheduling`
- `binance-futures-testnet-to-mainnet-promotion`
