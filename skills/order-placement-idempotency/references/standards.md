# Broker & Framework Coverage — order-placement-idempotency

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## The premise

**No broker below documents an idempotency guarantee for order placement.** Several accept a
client-supplied tag; none of the consulted docs states that submitting the same tag twice is
suppressed, de-duplicated, or rejected. Treat every entry in this table as a *correlation
handle for reconciliation*, never as duplicate protection supplied by the broker.

## Client-identifier fields

| Broker / Framework | Field | Documented constraints | Echoed back in the order book? | Confidence |
|---|---|---|---|---|
| Zerodha Kite Connect v3 | `tag` | "An optional tag to apply to an order to identify it (alphanumeric, max 20 chars)". Order objects carry a `tags` **array**, so more than one tag can be present. | Yes — order responses include `tags` | High (official docs) |
| Alpaca Trading API | `client_order_id` | "A unique identifier for the order. Automatically generated if not sent." Max length 128. Uniqueness is *reported* to be enforced against open orders, surfacing as HTTP 422 `client_order_id must be unique` — this appears in Alpaca's learn/community material, not in the API reference. | Yes — retrievable via `GET /v2/orders:by_client_order_id` | High for the field and length; **Medium** for the uniqueness/422 behaviour |
| Upstox API v2 | `tag` | "Tag for a particular order". Optional string. **No maximum length or character set is documented.** | Present in the order model | Medium — measure the accepted length against your own account before relying on 24 characters |
| Fyers API v3 | `orderTag` | An optional order tag appears in Fyers v3 order payloads (multileg examples and community usage). There is **no `client_order_id` field**, and no published length constraint was located. | Not verified | **Low** — verify field name, length and echo-back against the current Fyers v3 docs before use |
| ICICI Breeze API | `user_remark` | Accepted by `place_order` to "tag the order with user defined remark". An **open, unanswered issue on the official Breeze Python SDK repository** reports that the field "is not preserved at all. The API response completely disregards the user_remark field." | **Reported not echoed** | Medium — the report is a user issue, not a vendor statement; verify on your own account |
| IBKR TWS API | `orderId` (client) / `permId` (broker) | `orderId` is a client-assigned **integer**, seeded from the `nextValidId` callback and incremented; "the next valid identifier is persistent between TWS sessions", and with multiple client applications the id must exceed every id previously seen in `openOrder`/`orderStatus`. `permId` is the broker-assigned permanent id. | `orderId` and `permId` both appear in `openOrder`/`orderStatus` | High (official TWS API docs) |

### Consequences for key derivation

- The 24-character default of `make_idempotency_key()` **does not fit Kite Connect's 20-char
  `tag`**. Pass `max_len=20` (or `BROKER_KEY_MAX_LEN["zerodha_kite"]`) for Kite. A truncated
  key is a key reconciliation cannot match on.
- Kite documents `tag` as *alphanumeric*; a hex digest satisfies that, a base64 or UUID-with-
  hyphens key does not.
- IBKR takes no free-form client string. The "key" there is the integer `orderId` you
  allocate and record in the ledger; reconciliation matches on `permId`.
- Where the tag is not echoed back (Breeze as reported), set `broker_echoes_key=False` on the
  router. Absence from the order book then never resolves to `ABSENT`, and the router never
  re-sends on its own.

### Placement responses are not uniform

Kite Connect's success body is `{"status": "success", "data": {"order_id": "151220000000000"}}`,
and for auto-sliced orders `data` is an **array** — one placement request, several broker order
ids. A classifier that only accepts a flat `{"status": "SUCCESS"}` records a live Kite order as
rejected; one that reads a single `order_id` loses the sliced legs. Kite also documents that
successful placement "does not imply successful execution" — `PLACED` is not a fill.

## Regulatory & operational notes

| Jurisdiction | Instrument | What it actually says | Bearing on this skill |
|---|---|---|---|
| **US (SEC)** | 17 CFR § 240.15c3-5(c)(1)(ii), the Market Access Rule | Requires controls reasonably designed to "[p]revent the entry of erroneous orders, by rejecting orders that exceed appropriate price or size parameters, on an order-by-order basis or over a short period of time, **or that indicate duplicative orders**." | Duplicate-order prevention is named in the rule text. **Mandatory** for broker-dealers with market access; it binds the broker-dealer, not every algo operator. See `sec-rule-15c3-5-risk-controls-us`. |
| **EU (ESMA)** | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 15 | Mandates pre-trade controls on order entry, including price collars, maximum order values and volumes, maximum message limits, and **repeated automated execution throttles** that disable a strategy after a set number of repeated executions until a designated person re-enables it. | Adjacent, not identical: the throttle bounds repeated firing of a strategy; it is not a per-order duplicate check. A duplicate-submission bug is one way to trip the message and order-value limits. **Mandatory** for firms engaged in algorithmic trading under MiFID II. See `mifid-ii-algo-trading-compliance-eu`. |
| **India (SEBI)** | SEBI circular of 4 Feb 2025 on algorithmic trading, plus exchange implementation standards | Requires an **Exchange-provided unique algo ID** on API algo orders, for audit trail. | This identifies the *algorithm*, not the individual order, and is issued by the exchange — it is **not** an idempotency key and does not live in the same field. See `india-sebi-algo-trading-tagging-requirements`. |

Order audit-trail regimes (FINRA CAT in the US, exchange order logs in India) assume each order
event is attributable to one identifiable order. A duplicate placement is not merely an
economic loss; it produces two order records where the strategy intended one, which is what
makes it a reportable control failure rather than a bad trade.

## Sources

- Zerodha Kite Connect v3 — Orders API: <https://kite.trade/docs/connect/v3/orders/>
- Alpaca — Create an Order (API reference): <https://docs.alpaca.markets/reference/postorder>
- Alpaca — Orders at Alpaca: <https://docs.alpaca.markets/us/docs/orders-at-alpaca>
- Upstox API v2 — Place Order: <https://upstox.com/developer/api-documentation/place-order/>
- Fyers API v3 Python SDK: <https://pypi.org/project/fyers-apiv3/>
- ICICI Breeze Python SDK, issue #225 "user_remark field in place order":
  <https://github.com/Idirect-Tech/Breeze-Python-SDK/issues/225>
- IBKR TWS API — Placing Orders: <https://interactivebrokers.github.io/tws-api/order_submission.html>
- 17 CFR § 240.15c3-5: <https://www.law.cornell.edu/cfr/text/17/240.15c3-5>
- Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 15

Broker APIs change. Re-verify field names, length limits and echo-back behaviour against the
current documentation and against your own account before relying on any row above — see
`broker-api-versioning-migration-playbook`.
