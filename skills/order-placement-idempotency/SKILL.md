---
name: order-placement-idempotency
description: >-
  Use whenever a bot places, modifies, or cancels live orders and must guarantee it never double-executes an order due to retries, timeouts, or reconnects
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "fyers-api-v3", "zerodha-kite-connect", "icici-breeze-api"]
brokers_frameworks: ["Fyers API v3", "Zerodha Kite Connect", "ICICI Breeze API", "Upstox API v2", "Alpaca Trading API", "IBKR API"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this any time order-placement code includes retry logic, runs over an unreliable network, or could be re-triggered by a reconnect/restart. A naive retry-on-timeout ("call failed, try again") is the single most common cause of accidental duplicate live orders, because a timeout does not mean the order failed — it may mean the order succeeded and only the response was lost.

## Prerequisites

- Broker support for a client-supplied order tag/ID field (most brokers offer `tag`, `client_order_id`, or similar — verify the specific field name and length/character constraints per broker)
- A local order-intent ledger (DB table or durable log) that records intended orders before they are sent

## Workflow

1. Before sending any order to the broker, generate a unique idempotency key locally (e.g., `strategy_id + signal_timestamp + symbol + side` hashed, or a UUID stored against the intended order) and write an "intent" record to the local ledger with status `PENDING` — this write must happen before the network call, not after.
2. Attach this key to the broker call using whatever field the broker supports for client-side order identification. If the broker does not support this (some don't), fall back to step 5's reconciliation-only approach.
3. On send, handle three outcomes distinctly:
   - **Confirmed success** (broker returns an order ID) → update the local ledger to `PLACED` with the broker's order ID.
   - **Confirmed failure** (broker returns an explicit rejection, e.g. margin insufficient) → update ledger to `REJECTED`, safe to not retry blindly — but do inspect the rejection reason, since retrying a margin-rejected order after a margin top-up is legitimate while retrying a symbol-invalid rejection is not.
   - **Ambiguous/timeout** → do NOT immediately retry. Mark ledger status `UNKNOWN` and proceed to step 4.
4. On `UNKNOWN` status, before any retry, query the broker's order book/order-status endpoint filtered by the idempotency key (or by symbol+time window if the broker doesn't echo back client keys) to check whether the order actually landed. Only retry if reconciliation confirms the order does not exist on the broker side.
5. If the broker provides no client-order-ID mechanism at all, reconciliation must be done by matching recent orders on (symbol, side, quantity, price, timestamp window) against the local intent ledger before allowing any retry — accept that this is weaker than a broker-native idempotency key and document the residual risk.
6. On bot restart/reconnect, always reconcile the full set of `PENDING`/`UNKNOWN` ledger entries against the live broker order book before resuming new order generation — a bot that starts placing new signals without first reconciling in-flight orders from before the restart can double up on positions.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Retrying on any exception without distinguishing "confirmed rejected" from "network timeout, unknown outcome" — this is the core bug this skill prevents.
- Relying solely on the broker's client-order-ID field without a local ledger — if the broker's own state gets corrupted or delayed, there's no independent source of truth to reconcile against.
- Reconciling only order IDs the bot itself generated in the current process lifetime, missing orders placed just before a crash/restart.
- Assuming idempotency keys are globally unique forever — some brokers only guarantee uniqueness within a trading day or a rolling window; regenerate keys per session if the broker's uniqueness guarantee is scoped.

## Verification

- Inject an artificial timeout (mock the HTTP client to drop the response after the broker has processed the request, e.g. using a broker sandbox/paper environment) and confirm the bot's reconciliation step correctly detects the order exists and does not place a duplicate.
- Confirm a full restart mid-order-cycle (kill the process after intent-write but before confirmed response) results in exactly one order on the broker side after the bot resumes and reconciles.
- Audit the local ledger after a multi-day live run and confirm every `PLACED` entry has a corresponding broker order ID, and no order exists on the broker side without a matching ledger entry.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `multi-broker-rate-limit-handling`
- `paper-to-live-promotion-checklist`
