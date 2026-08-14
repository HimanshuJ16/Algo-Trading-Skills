# Broker Integration Standards — broker-api-idempotent-cancel-requests

## Category

`broker-integration` — see top-level `mappings/` directory.

## Response classification

| Response / condition | Status | Terminal? | Caller obligation |
|---|---|---|---|
| HTTP 200 / 202 / 204 | `PENDING_CANCEL` | No | Await the order-state stream; the order can still fill |
| HTTP 2xx, synchronous-cancel broker (opt-in) | `CANCELLED` | Yes | Order is dead |
| HTTP 4xx, "too late to cancel" / "already filled" | `FILLED_BEFORE_CANCEL` | Yes | Book the fill |
| HTTP 4xx, "already cancelled" | `ALREADY_CANCELLED` | Yes | Order is dead |
| HTTP 404, or "unknown order" / "no such order" | `ORDER_UNKNOWN` | No | Reconcile — this is not proof of cancellation |
| Other HTTP 4xx (incl. bare `422`) | `REJECTED` | No | Diagnose, then retry under a new cancel id |
| HTTP 5xx / 408 / 429 / 418 exhausted, or transport error | `UNKNOWN` | No | Reconcile; re-dispatch the same cancel id |

Retryable statuses: any 5xx, `408`, `418`, `429`, and transport exceptions. All other 4xx are
terminal for the attempt.

## FIX 4.4 mapping

A cancel is issued as an `OrderCancelRequest` (35=F) carrying a **new** `ClOrdID` (tag 11)
and referencing the original in `OrigClOrdID` (tag 41). The request is not the outcome:

| FIX construct | Value | Meaning |
|---|---|---|
| `OrdStatus` (39) | `6` | Pending Cancel — "e.g. result of Order Cancel Request `<F>`" |
| `OrdStatus` (39) | `4` | Canceled — the terminal state, delivered by `ExecutionReport` (35=8) |
| `CxlRejReason` (102) | `0` | Too late to cancel → `FILLED_BEFORE_CANCEL` |
| `CxlRejReason` (102) | `1` | Unknown order → `ORDER_UNKNOWN` (ambiguous; reconcile) |
| `CxlRejReason` (102) | `3` | Order already in Pending Cancel or Pending Replace status → `REJECTED` |
| `CxlRejReason` (102) | `6` | Duplicate `ClOrdID` received → `REJECTED` |
| `OrderCancelReject` (35=9) | — | The cancel was refused; the order is presumed still working |

The operative rule: reconcile local order state from incoming `ExecutionReport` (35=8)
messages, never from the dispatch or acknowledgement of the cancel request itself. Value `6`
existing as a distinct state from `4` is the protocol saying exactly that.

## Broker-specific cancel semantics

| Broker | Endpoint | Accepted | Not cancellable | Cancel is… |
|---|---|---|---|---|
| Alpaca | `DELETE /v2/orders/{order_id}` | `204` | `422` "The order status is not cancelable." | **Asynchronous** — order sits in `pending_cancel` until the execution venue confirms |
| Binance Spot | `DELETE /api/v3/order` | `200` with the order payload in state `CANCELED` | `400` + `-2011` `CANCEL_REJECTED` / `-2013` `NO_SUCH_ORDER` | **Synchronous** — a candidate for `treat_ack_as_cancelled=True` |
| Zerodha Kite Connect | `DELETE /orders/:variety/:order_id` | `200` with `{"data": {"order_id": …}}` | broker-specific | **Asynchronous** — state arrives via postbacks |

Verify these per broker and per API version before relying on them; cancel semantics are one
of the things that changes across versions (see `broker-api-versioning-migration-playbook`).

## Rate limiting and cancel storms

Binance documents the escalation explicitly: HTTP `429` is returned on breaching a request
rate limit, and HTTP `418` when an IP has been "auto-banned for continuing to send requests
after receiving `429` codes". A `Retry-After` header accompanies both — for `429`, the wait
needed to avoid a ban; for `418`, the remaining ban duration.

The operational consequence is specific to cancels: an undeduplicated cancel retry loop can
get the IP banned, and a banned client cannot cancel *anything*. De-duplication and jittered
backoff are risk controls here, not politeness.

RFC 9110 Section 10.2.3 defines `Retry-After` as either delay-seconds or an HTTP-date. Both
forms occur in practice; parsing only the integer form is a common defect.

## Indeterminate outcomes

Binance's REST general API information states the rule directly for 5xx responses:

> "HTTP `5XX` return codes are used for internal errors; the issue is on Binance's side. It is
> important to **NOT** treat this as a failure operation; the execution status is **UNKNOWN**
> and could have been a success."

This is why `UNKNOWN` and `ORDER_UNKNOWN` are excluded from the idempotency cache: caching an
indeterminate outcome as terminal converts one lost response into an order that can never be
cancelled again through the same cancel id.

## Sources

- FIX 4.4 specification — `OrdStatus` (tag 39) and `CxlRejReason` (tag 102) enumerations.
  <https://www.onixs.biz/fix-dictionary/4.4/tagNum_39.html>,
  <https://www.onixs.biz/fix-dictionary/4.4/tagNum_102.html>
- Alpaca Trading API — "Delete Order by ID" (`204` accepted / `422` not cancelable).
  <https://docs.alpaca.markets/reference/deleteorderbyorderid-1>
- Alpaca Trading API — order statuses, including `pending_cancel` ("The order is waiting to be
  canceled") and the aged-order note that orders "will remain in `pending_cancel` until
  canceled by the execution venue". <https://docs.alpaca.markets/us/docs/orders-at-alpaca>
- Binance Spot REST API — general API information (4XX / `429` / `418` / 5XX semantics and
  `Retry-After`).
  <https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-api-information>
- Binance Spot REST API — error codes `-2011` (`CANCEL_REJECTED`), `-2013` (`NO_SUCH_ORDER`).
  <https://developers.binance.com/docs/binance-spot-api-docs/errors>
- Zerodha Kite Connect v3 — orders and cancellation; "Successful placement of an order via the
  API does not imply its successful execution." <https://kite.trade/docs/connect/v3/orders/>
- Zerodha Kite Connect v3 — postbacks (asynchronous order-state updates).
  <https://kite.trade/docs/connect/v3/postbacks/>
- RFC 9110, "HTTP Semantics", Section 10.2.3 (`Retry-After`).
  <https://www.rfc-editor.org/rfc/rfc9110#field.retry-after>

## Regulatory note

This skill makes no jurisdiction-specific compliance claim. Reliable order cancellation is a
component of controls that several regimes do mandate — kill functionality, order-to-trade
ratio management, pre-trade risk controls — but those obligations are stated in the skills
that own them (`kill-switch-and-drawdown-circuit-breakers`,
`order-to-trade-ratio-fee-penalty-avoidance`, `sec-rule-15c3-5-risk-controls-us`,
`mifid-ii-algo-trading-compliance-eu`). Do not cite this document as evidence of compliance
with any of them.
