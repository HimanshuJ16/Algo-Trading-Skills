---
name: borsa-istanbul-api-integration
description: >-
  Use when routing orders to Borsa Istanbul BISTECH over FIX 5.0 SP2 and the order
  lifecycle must be modelled correctly: NewOrderSingle, the two possible answers to a
  cancel request, and execution reports applied idempotently across resends.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: broker-integration, borsa-istanbul, fix-protocol, bistech, order-lifecycle
  brokers_frameworks: "BISTECH FIX 5.0 SP2; BISTECH OUCH; BISTECH ITCH; QuickFIX"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building order routing to Borsa Istanbul's BISTECH platform and you
need the **order lifecycle modelled correctly**: NewOrderSingle (MsgType=D), Order Cancel
Request (MsgType=F) and its two possible answers, and ExecutionReport (MsgType=8)
application that survives resends.

BISTECH runs on Nasdaq's Genium INET technology. BIST offers FIX and OUCH for order entry
and ITCH and TIP for market data; Borsa İstanbul states it supports **FIX 5.0 SP2**, and
members must certify their software (or use a certified application) before production
access.

## When NOT to Use

- **As a FIX gateway.** `scripts/borsa_istanbul_api_integration.py` is an in-memory order
  state machine with a *simulated* session layer. It opens no sockets, encodes and decodes
  no FIX messages, assigns no FIX sequence numbers, and persists nothing across restarts.
  `connect()` sets a flag. Use a real engine (QuickFIX or a BIST-certified application) for
  transport, and use this module to model the lifecycle and drive state from the reports
  that engine decodes.
- **For latency-sensitive order entry.** BIST's low-level binary OUCH protocol exists for
  that; FIX is the broader-access, higher-overhead option. Do not benchmark a
  latency-critical path against a FIX design.
- **For market data.** Order entry only. ITCH/TIP are separate protocols with separate
  certification.
- **As a substitute for certification.** Passing these unit tests is not BISTECH
  certification and grants no production access.

## Prerequisites

- Python 3.9+.
- A real FIX engine for transport, and network connectivity to BIST's FIX gateways or the
  BISTECH simulator environment.
- Approved SenderCompID and TargetCompID issued by Borsa Istanbul.
- BISTECH FIX certification for the market you are trading (required for production).
- The BISTECH specification documents for **your** market. Message-level details, accepted
  TimeInForce values, required party/account tags and session phases are venue- and
  market-specific; this skill deliberately does not hard-code them.

## Workflow

1. **Configure and establish the session.** `BISTConfig` carries CompIDs, host, port and
   heartbeat interval. `connect()` validates them — an out-of-range port or an empty
   CompID is rejected before anything is attempted, because a FIX message without both
   CompIDs cannot log on.
2. **Build and submit the order.** `submit_order()` refuses anything the venue or the
   protocol would reject: non-finite or non-positive quantity, a limit order without a
   positive price, a market order that carries a price (FIX forbids Price on OrdType=Market),
   an empty symbol, and — critically — a **duplicate ClOrdID**. Reusing a ClOrdID would
   overwrite a live order's record and discard its accumulated fills, so it raises rather
   than replacing.
3. **Apply fills as they arrive.** `simulate_execution_report(client_order_id, filled_qty,
   exec_price, exec_id=...)` takes `filled_qty` as **LastQty** (this fill), not cumulative.
   **Always pass `exec_id`.** After a sequence gap the counterparty resends messages, and
   ExecID is the only thing distinguishing a resent report from a new one; without it a
   resend double-counts the fill and corrupts the average price.
4. **To cancel, send the request — then wait.** `cancel_order()` sends an Order Cancel
   Request and moves the order to `PENDING_CANCEL`. **The order is still live at the venue
   at this point and can still fill.** Do not treat `PENDING_CANCEL` as cancelled, do not
   release its risk budget, and do not reuse its ClOrdID.
5. **Resolve the cancel on the venue's answer, not your own.** An ExecutionReport with
   ExecType=Canceled → `confirm_cancel()`. An Order Cancel Reject (MsgType=9) → `reject_cancel()`,
   which returns the order to `PARTIALLY_FILLED` or `NEW` according to its fill state,
   because a rejected cancel means the order was never cancelled. The most common reason
   for a reject is that the order completed or was already inactive before the request
   landed.
6. **Treat a refused overfill as an incident, not a warning.** If a report would push
   cumulative quantity past the order quantity, it is rejected and logged at ERROR. That
   means a duplicate escaped deduplication or the venue sent something impossible —
   reconcile against the venue before trading on the position.

> Full session and order-routing sequence: see `references/workflows.md`.
> Protocol and symbology conventions: see `references/standards.md`.
> Pre-production readiness checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating an Order Cancel Request as a cancellation.** MsgType=F *requests* cancellation
  of the remaining quantity. Until the venue answers with ExecType=Canceled or an Order
  Cancel Reject, the order is working and can fill. Marking it cancelled locally and then
  discarding subsequent execution reports as "terminal" silently loses real fills and
  leaves your position short of the venue's.
- **Applying execution reports without ExecID deduplication.** Resend Request (MsgType=2)
  recovery is a normal part of FIX session management, and it replays application messages.
  A handler with no ExecID memory double-counts every replayed fill.
- **Accepting cumulative quantity beyond the order quantity.** An overfill is never
  legitimate; absorbing it silently converts a message-handling bug into a phantom position
  and a wrong average price.
- **Reusing a ClOrdID.** BIST requires it to be unique. Reuse makes the venue's reports
  ambiguous and, locally, overwrites the fill state of the order still working under that ID.
- **Validating quantity with `qty <= 0` alone.** NaN fails every comparison, so a NaN
  quantity passes that check and gets routed.
- **Sending Price on a market order.** FIX forbids Price on OrdType=Market. Some gateways
  reject it, others silently ignore it and fill you at a price you did not intend.
- **Naive UTC timestamps.** BIST operates on Europe/Istanbul time. Timestamps that carry no
  timezone silently misalign against venue timestamps in reconciliation and forensics.
- **Assuming this module manages FIX sequence numbers.** It does not. Sequence assignment,
  gap fill, Resend Request handling and persistence across restarts belong to your FIX
  engine, and getting them wrong is its own failure mode.
- **Connecting to the wrong environment.** Simulator and production differ in CompIDs,
  endpoints and TLS/VPN configuration; a config mix-up sends live orders from a test run.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/borsa-istanbul-api-integration/scripts`
- Assert the cancel-race invariant explicitly in your own integration: send a cancel
  request, deliver a fill before any venue answer, and confirm the fill is applied and the
  order remains `PENDING_CANCEL`. This is the defect that costs money; test it directly.
- Replay a captured ExecutionReport twice with the same ExecID and confirm cumulative
  quantity and average price are unchanged.
- Reconcile cumulative filled quantity and average price against the venue's own
  end-of-day trade file — the local state machine is only as good as the reports fed to it.
- Run BIST's FIX certification scenarios against the BISTECH simulator before production.
  Unit tests do not substitute for certification.

## Related Skills

- `fix-protocol-session-management-across-venues`
- `broker-api-idempotent-cancel-requests`
- `order-placement-idempotency`
- `nasdaq-totalview-itch-feed-parsing`
- `cme-stp-fix-and-ilink2-tag-value-encoding`
