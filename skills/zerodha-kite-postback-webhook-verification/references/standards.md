# Broker & Framework Coverage — zerodha-kite-postback-webhook-verification

Each row records what the broker's **own documentation** states, and marks separately
anything that is only community- or forum-reported. Broker APIs change without notice —
re-verify before relying on any row. Sources are listed at the bottom.

## Zerodha Kite Connect v3 — the scheme this skill implements

| Property | What Kite documents |
|---|---|
| Transport | Raw JSON HTTP POST body to the app's registered postback URL. No signature headers. Zerodha staff, on the developer forum: "The data comes as raw POST body" |
| Signature | `checksum` field = "the SHA-256 hash of (`order_id` + `order_timestamp` + `api_secret`)" — a plain digest of a concatenation, **not** an HMAC |
| Fields authenticated | `order_id` and `order_timestamp` only |
| Fields **not** authenticated | `status`, `filled_quantity`, `average_price`, `pending_quantity`, `unfilled_quantity`, and every other body field |
| Timestamp format | `order_timestamp`, e.g. `"2022-03-03 09:24:25"`. **No timezone marker is present and none is documented** |
| Trigger events | `COMPLETE`, `CANCEL`, `REJECTED`, and `UPDATE`. "An `UPDATE` postback is triggered when an open order is modified or when there's a partial fill" |
| Endpoint port | Not in the API docs; a Zerodha engineer states on the forum that outbound connections are limited to ports 80 and 443, so "please use default SSL port (443)" |
| Retry / delivery / ordering guarantee | **Not documented.** Neither the API docs nor the forum threads reviewed state a retry policy, an at-least-once guarantee, or an ordering guarantee |
| Expected HTTP response | Not documented |

### The consequence that shapes the implementation

Because the digest's pre-image is only `order_id + order_timestamp + api_secret`, a
checksum that verifies proves the sender holds the API secret and is talking about a
specific order at a specific instant. It proves nothing about the rest of the body.
An intercepted postback whose `filled_quantity` is rewritten produces the same checksum
and verifies. The mitigation is not cryptographic — it is to treat an accepted postback
as a *trigger* and read authoritative state from `GET /orders/:order_id`, which the Kite
docs describe as returning the order's full status-transition history.

### Timezone

Kite's docs specify no timezone for `order_timestamp`. The official Python client,
`pykiteconnect`, parses these 19-character strings with `dateutil.parser.parse` and
leaves the result **timezone-naive** — it attaches nothing. The value is exchange-local
time for an Indian venue, i.e. IST. `scripts/postback_verifier.py` therefore defaults to
a fixed UTC+05:30 offset (exact: India observes no DST) and exposes `timestamp_tz` so a
consumer can override it rather than inherit a server's local zone. Interpreting the
naive string as UTC adds a constant 5.5 hours of apparent drift and rejects every
genuine postback.

## Other Indian brokers — do not assume this scheme transfers

The signature schemes below differ from Kite's; this skill's verifier is **not**
portable to them without reading each broker's own documentation.

| Broker | Documented postback authentication | Confidence |
|---|---|---|
| Upstox | The current Upstox webhook documentation describes the payload and delivery but specifies **no signature or checksum scheme**, and states the webhook endpoint "should not require authentication". Secondary sources describe an MD5 checksum on an earlier Upstox postback API | **Low — no signature verified in current primary docs.** Treat an Upstox webhook as unauthenticated unless their docs say otherwise |
| Fyers | Fyers publishes a Postback (Webhooks) section, but no signature-verification specification was reachable in the primary docs at the time of writing | **Unverified.** No claim made here |

An earlier revision of this file asserted `HMAC-SHA256(payload_body, api_secret)` for
Upstox and "token authorization & SHA-256 payload validation" for Fyers. Neither is
supported by the brokers' own documentation and both have been removed.

For the generic pattern that most non-Indian venues do use — HMAC-SHA-256 over the raw
body, carried in a header — see `webhook-based-order-fill-notifications`.

## Regulatory & Operational Notes

No regulator mandates a specific webhook signature scheme; the requirement here is an
engineering one imposed by the broker. Two adjacent obligations do apply to the receiver:

- **Audit trail.** Order-state changes ingested from postbacks feed the records a firm
  must retain. Log the outcome class, order id and event fingerprint for every delivery,
  accepted or rejected. See `record-retention-periods-by-jurisdiction`.
- **Pre-trade risk controls.** Any order this handler causes to be submitted (a hedge
  triggered by a fill notification, say) is still subject to the firm's pre-trade
  controls. A postback handler must not be a path that bypasses them. See
  `sec-rule-15c3-5-risk-controls-us` for the analogous US framing.

Web-application security guidance for webhook receivers — verify the signature before
processing, fail closed, rate-limit, log rejections — is general OWASP practice rather
than a named standard with a citable clause; it is stated here as practice, not as a
requirement traced to a specific document.

## Sources

- Kite Connect v3 — Postbacks / WebHooks: <https://kite.trade/docs/connect/v3/postbacks/>
  (checksum formula, payload fields and example, `UPDATE`-on-partial-fill statement)
- Kite Connect v3 — Orders: <https://kite.trade/docs/connect/v3/orders/>
  (`GET /orders/:order_id` order history, status values, postbacks-vs-polling guidance)
- Kite Connect developer forum, "Postback (Webhooks)":
  <https://kite.trade/forum/discussion/98/postback-webhooks>
  (raw POST body; ports 80/443 only — staff replies, not API documentation)
- `pykiteconnect` (official Python client), `kiteconnect/connect.py`:
  <https://github.com/zerodha/pykiteconnect> (19-character datetime parsing, naive result)
- Upstox Developer API — Webhook:
  <https://upstox.com/developer/api-documentation/webhook/>
